#!/usr/bin/python
# -*- coding: utf-8 -*-

DOCUMENTATION = '''
---
module: oracle_gi_facts
short_description: Returns some facts about Grid Infrastructure environment
description:
  - Returns some facts about Grid Infrastructure environment
  - Must be run on a remote host
version_added: "2.4"
options:
  oracle_home:
    description:
      - Grid Infrastructure home, can be absent if ORACLE_HOME environment variable is set
    required: false
notes:
  - Oracle Grid Infrastructure 12cR1 or later required
  - Must be run as (become) GI owner
author:
  - Ilmar Kerm, ilmar.kerm@gmail.com, @ilmarkerm
  - Ivan Brezina
'''

EXAMPLES = '''
---
- name: Return GI facts
  oracle_gi_facts:
  register: _oracle_gi_facts

- name: GI facts
  debug: var=_oracle_gi_facts
'''

import os, re
from socket import gethostname, getfqdn

# The following is to make the module usable in python 2.6 (RHEL6/OEL6)
# Source: http://pydoc.net/pep8radius/0.9.0/pep8radius.shell/
try:
    from subprocess import check_output, CalledProcessError
except ImportError:  # pragma: no cover
    # python 2.6 doesn't include check_output
    # monkey patch it in!
    import subprocess
    STDOUT = subprocess.STDOUT

    def check_output(*popenargs, **kwargs):
        if 'stdout' in kwargs:  # pragma: no cover
            raise ValueError('stdout argument not allowed, it will be overridden.')
        process = subprocess.Popen(stdout=subprocess.PIPE, *popenargs, **kwargs)
        output, _ = process.communicate()
        retcode = process.poll()
        if retcode:
            cmd = kwargs.get("args")
            if cmd is None:
                cmd = popenargs[0]
            raise subprocess.CalledProcessError(retcode, cmd, output=output)
        return output
    subprocess.check_output = check_output

    # overwrite CalledProcessError due to `output`
    # keyword not being available (in 2.6)
    class CalledProcessError(Exception):

        def __init__(self, returncode, cmd, output=None):
            self.returncode = returncode
            self.cmd = cmd
            self.output = output

        def __str__(self):
            return "Command '%s' returned non-zero exit status %d" % (
                self.cmd, self.returncode)
    subprocess.CalledProcessError = CalledProcessError


def is_executable(fpath):
    return os.path.isfile(fpath) and os.access(fpath, os.X_OK)


def exec_program_lines(arguments):
    try:
        output = check_output(arguments)
        return [line.strip().decode() for line in output.splitlines()]
    except CalledProcessError:
        # Just ignore the error
        return ['']

def exec_program(arguments):
    return exec_program_lines(arguments)[0]

def hostname_to_fqdn(hostname):
    if "." not in hostname:
        return getfqdn(hostname)
    else:
        return hostname

def local_listener():
    global srvctl, shorthostname, iscrs, vips
    args = [srvctl, 'status', 'listener']
    if iscrs:
        args += ['-n', shorthostname]
    listeners_out = exec_program_lines(args)
    re_listener_name = re.compile('Listener (.+) is enabled')
    listeners = []
    out = []
    for line in listeners_out:
        if "is enabled" in line:
            m = re_listener_name.search(line)
            listeners.append(m.group(1))
    for l in listeners:
        config = {}
        output = exec_program_lines([srvctl, 'config', 'listener', '-l', l])
        for line in output:
            if line.startswith('Name:'):
                config['name'] = line[6:]
            elif line.startswith('Type:'):
                config['type'] = line[6:]
            elif line.startswith('Network:'):
                config['network'] = line[9:line.find(',')]
            elif line.startswith('End points:'):
                config['endpoints'] = line[12:]
                for proto in config['endpoints'].split('/'):
                    p = proto.split(':')
                    config[p[0].lower()] = p[1]
        if "network" in config.keys():
            config['address'] = vips[config['network']]['fqdn']
            config['ipv4'] = vips[config['network']]['ipv4']
            config['ipv6'] = vips[config['network']]['ipv6']
        out.append(config)
    return out


def scan_listener():
    global srvctl, shorthostname, iscrs, networks, scans
    out = {}
    for n in networks.keys():
        output = exec_program_lines([srvctl, 'config', 'scan_listener', '-k', n])
        for line in output:
            endpoints = None
            # 19c
            m = re.search('Endpoints: (.+)', line)
            if m is not None:
                endpoints = m.group(1)
            else:
                # 18c, 12c
                m = re.search('SCAN Listener (.+) exists. Port: (.+)', line)
                if m is not None:
                    endpoints = m.group(2)
            if endpoints:
                out[n] = {'network': n, 'scan_address': scans[n]['fqdn'], 'endpoints': endpoints, 'ipv4': scans[n]['ipv4'], 'ipv6': scans[n]['ipv6']}
                for proto in endpoints.split('/'):
                    p = proto.split(':')
                    out[n][p[0].lower()] = p[1]
                break
    return out

def get_networks():
    global srvctl, shorthostname, iscrs
    out = {}
    item = {}
    output = exec_program_lines([srvctl, 'config', 'network'])
    for line in output:
        m = re.search('Network ([0-9]+) exists', line)
        if m is not None:
            if "network" in item.keys():
                out[item['network']] = item
            item = {'network': m.group(1)}
        elif line.startswith('Subnet IPv4:'):
            item['ipv4'] = line[13:]
        elif line.startswith('Subnet IPv6:'):
            item['ipv6'] = line[13:]
    if "network" in item.keys():
        out[item['network']] = item
    return out

def get_vips():
    global srvctl, shorthostname, iscrs
    output = exec_program_lines([srvctl, 'config', 'vip', '-n', shorthostname])
    vip = {}
    out = {}
    for line in output:
        if line.startswith('VIP exists:'):
            if "network" in vip.keys():
                out[vip['network']] = vip
            vip = {}
            m = re.search('network number ([0-9]+),', line)
            vip['network'] = m.group(1)
        elif line.startswith('VIP Name:'):
            vip['name'] = line[10:]
            vip['fqdn'] = hostname_to_fqdn(vip['name'])
        elif line.startswith('VIP IPv4 Address:'):
            vip['ipv4'] = line[18:]
        elif line.startswith('VIP IPv6 Address:'):
            vip['ipv6'] = line[18:]
    if "network" in vip.keys():
        out[vip['network']] = vip
    return out


def get_scans():
    global srvctl, shorthostname, iscrs
    out = {}
    item = {}
    output = exec_program_lines([srvctl, 'config', 'scan', '-all'])
    for line in output:
        if line.startswith('SCAN name:'):
            if "network" in item.keys():
                out[item['network']] = item
            m = re.search('SCAN name: (.+), Network: ([0-9]+)', line)
            item = {'network': m.group(2), 'name': m.group(1), 'ipv4': [], 'ipv6': []}
            item['fqdn'] = hostname_to_fqdn(item['name'])
        else:
            m = re.search('SCAN [0-9]+ (IPv[46]) VIP: (.+)', line)
            if m is not None:
                item[m.group(1).lower()] += [m.group(2)]
    if "network" in item.keys():
        out[item['network']] = item
    return out


# Ansible code
def main():
    global module, shorthostname, hostname, srvctl, crsctl, cemutlo, iscrs, vips, networks, scans
    msg = ['']
    module = AnsibleModule(
        argument_spec=dict(
            oracle_home=dict(required=False, aliases = ['oh'])
        ),
        supports_check_mode=True
    )
    # Preparation
    facts = {}
    if module.params["oracle_home"]:
        os.environ['ORACLE_HOME'] = module.params["oracle_home"]
    else:
        ohomes = oracle_homes()
        ohomes.list_crs_instances()
        ohomes.list_processes()
        ohomes.parse_oratab()
        
        if ohomes.crs_home:
            os.environ['ORACLE_HOME'] = ohomes.crs_home

    if 'ORACLE_HOME' in os.environ:
        srvctl = os.path.join(os.environ['ORACLE_HOME'], 'bin', 'srvctl')
        crsctl = os.path.join(os.environ['ORACLE_HOME'], 'bin', 'crsctl')
        cemutlo = os.path.join(os.environ['ORACLE_HOME'], 'bin', 'cemutlo')
    else:
        module.fail_json(changed=False, msg="Could not find GI home. I can't find executables srvctl or crsctl")
            
    olsnodes = os.path.join(os.environ['ORACLE_HOME'], 'bin', 'olsnodes')
    # Lets assume that empty output form olsnodes means we're on Oracle Restart
    iscrs = bool(exec_program_lines([olsnodes]))

    hostname = gethostname()
    shorthostname = hostname.split('.')[0]

    # Cluster name
    facts.update({'clustername': exec_program([cemutlo, '-n'])})

    # Cluster version
    if iscrs:
        version = exec_program([crsctl, 'query', 'crs', 'activeversion'])
    else:
        for i in ['releaseversion', 'releasepatch', 'softwareversion', 'softwarepatch']:
            version = exec_program([crsctl, 'query', 'has', i])
            m = re.search('\[([0-9\.]+)\]$', version)
            if m:
                facts.update({i: m.group(1)})
                facts.update({"version": m.group(1)}) # for backward compatibility
            else:
                facts.update({i: version})

    # VIPS
    vips = get_vips()
    facts.update({'vip': list(vips.values())})
    # Networks
    networks = get_networks()
    facts.update({'network': list(networks.values())})
    # SCANs
    scans = get_scans()
    facts.update({'scan': list(scans.values())})
    # Listener
    facts.update({'local_listener': local_listener()})
    facts.update({'scan_listener': list(scan_listener().values()) if iscrs else []})
    # Databases
    facts.update({'database_list': exec_program_lines([srvctl, 'config', 'database'])})
    # ORACLE_CRS_HOME
    facts.update({'oracle_crs_home': os.environ['ORACLE_HOME']})
    # Output
    module.exit_json(msg=", ".join(msg), changed=False, ansible_facts={"oracle_gi_facts": facts})


from ansible.module_utils.basic import *

# In these we do import from local project sub-directory <project-dir>/module_utils
# While this file is placed in <project-dir>/library
# No collections are used
# try:
#    from ansible.module_utils.oracle_homes import oracle_homes
# except:
#    pass

# In these we do import from collections
try:
    from ansible_collections.ibre5041.ansible_oracle_modules.plugins.module_utils.oracle_homes import *
except:
    pass

if __name__ == '__main__':
    main()
