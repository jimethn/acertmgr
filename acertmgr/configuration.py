#!/usr/bin/env python
# -*- coding: utf-8 -*-

# config - acertmgr config parser
# Copyright (c) Markus Hauschild & David Klaftenegger, 2016.
# Copyright (c) Rudolf Mayerhofer, 2019.
# available under the ISC license, see LICENSE

import argparse
import copy
import hashlib
import io
import json
import os
import sys

try:
    import idna
except ImportError:
    pass

# Backward compatiblity for older versions/installations of acertmgr
LEGACY_WORK_DIR = "/etc/acme"
LEGACY_CONF_FILE = os.path.join(LEGACY_WORK_DIR, "acme.conf")
LEGACY_CONF_DIR = os.path.join(LEGACY_WORK_DIR, "domains.d")

# Configuration defaults to use if not specified otherwise
DEFAULT_CONF_FILE = "/etc/acertmgr/acertmgr.conf"
DEFAULT_CONF_DIR = "/etc/acertmgr"
DEFAULT_KEY_LENGTH = 4096  # bits
DEFAULT_TTL = 30  # days
DEFAULT_API = "v2"
DEFAULT_AUTHORITY = "https://acme-v02.api.letsencrypt.org"
LEGACY_API = "v1"
LEGACY_AUTHORITY = "https://acme-v01.api.letsencrypt.org"
LEGACY_AUTHORITY_TOS_AGREEMENT = "true"


# @brief augment configuration with defaults
# @param domainconfig the domain configuration
# @param defaults the default configuration
# @return the augmented configuration
def complete_action_config(domainconfig, config):
    defaults = config['defaults']
    domainconfig['ca_file'] = config['ca_file']
    domainconfig['cert_file'] = config['cert_file']
    domainconfig['key_file'] = config['key_file']
    for name, value in defaults.items():
        if name not in domainconfig:
            domainconfig[name] = value
    if 'action' not in domainconfig:
        domainconfig['action'] = None
    return domainconfig


# @brief update config[name] with value from localconfig>globalconfig>default
def update_config_value(config, name, localconfig, globalconfig, default):
    values = [x for x in localconfig if name in x]
    if len(values) > 0:
        config[name] = values[0][name]
    else:
        config[name] = globalconfig.get(name, default)


# @brief convert domain list to idna representation (if applicable
def idna_convert(domainlist):
    if 'idna' in sys.modules and any(ord(c) >= 128 for c in ''.join(domainlist)):
        domaintranslation = {}
        for domain in domainlist:
            if any(ord(c) >= 128 for c in domain):
                # Translate IDNA domain name from a unicode domain (handle wildcards separately)
                if domain.startswith('*.'):
                    idna_domain = "*.{}".format(idna.encode(domain[2:]).decode('utf-8'))
                else:
                    idna_domain = idna.encode(domain).decode('utf-8')
                domaintranslation[idna_domain] = domain
        return domaintranslation
    else:
        if 'idna' not in sys.modules:
            print("Unicode domain found but IDNA names could not be translated due to missing idna module")
        return {}


# @brief load the configuration from a file
def parse_config_entry(entry, globalconfig, runtimeconfig):
    config = dict()

    # Basic domain information
    config['domains'], localconfig = entry
    config['domainlist'] = config['domains'].split(' ')
    config['id'] = hashlib.md5(config['domains'].encode('utf-8')).hexdigest()

    # Convert unicode to IDNA domains
    config['domaintranslation'] = idna_convert(config['domainlist'])
    if len(config['domaintranslation']) > 0:
        config['domainlist'] = config['domaintranslation'].values()
        config['domains'] = ' '.join(config['domainlist'])

    # Action config defaults
    config['defaults'] = globalconfig.get('defaults', {})

    # API version
    update_config_value(config, 'api', localconfig, globalconfig, DEFAULT_API)

    # Certificate authority
    update_config_value(config, 'authority', localconfig, globalconfig, DEFAULT_AUTHORITY)

    # Certificate authority ToS agreement
    update_config_value(config, 'authority_tos_agreement', localconfig, globalconfig,
                        runtimeconfig['authority_tos_agreement'])

    # Certificate authority contact email addresses
    update_config_value(config, 'authority_contact_email', localconfig, globalconfig, None)

    # Account key
    update_config_value(config, 'account_key', localconfig, globalconfig,
                        os.path.join(runtimeconfig['work_dir'], "account.key"))

    # Certificate directory
    update_config_value(config, 'cert_dir', localconfig, globalconfig, runtimeconfig['work_dir'])

    # TTL days
    update_config_value(config, 'ttl_days', localconfig, globalconfig, DEFAULT_TTL)
    config['ttl_days'] = int(config['ttl_days'])

    # Revoke old certificate with reason superseded after renewal
    update_config_value(config, 'cert_revoke_superseded', localconfig, globalconfig, "false")

    # Use a static cert request
    update_config_value(config, 'csr_static', localconfig, globalconfig, "false")

    # SSL cert request location
    update_config_value(config, 'csr_file', localconfig, globalconfig,
                        os.path.join(config['cert_dir'], "{}.csr".format(config['id'])))

    # SSL cert location (with compatibility to older versions)
    if 'server_cert' in globalconfig:
        print("WARNING: Legacy configuration directive 'server_cert' used. Support will be removed in 1.0")
    update_config_value(config, 'cert_file', localconfig, globalconfig,
                        globalconfig.get('server_cert',
                                         os.path.join(config['cert_dir'], "{}.crt".format(config['id']))))

    # SSL key location (with compatibility to older versions)
    if 'server_key' in globalconfig:
        print("WARNING: Legacy configuration directive 'server_key' used. Support will be removed in 1.0")
    update_config_value(config, 'key_file', localconfig, globalconfig,
                        globalconfig.get('server_key',
                                         os.path.join(config['cert_dir'], "{}.key".format(config['id']))))

    # SSL key length (if key has to be (re-)generated, converted to int)
    update_config_value(config, 'key_length', localconfig, globalconfig, DEFAULT_KEY_LENGTH)
    config['key_length'] = int(config['key_length'])

    # SSL CA location
    ca_files = [x for x in entry if 'ca_file' in x]
    if len(ca_files) > 0:
        config['static_ca'] = True
        config['ca_file'] = ca_files[0]
    elif 'server_ca' in globalconfig:
        print("WARNING: Legacy configuration directive 'server_ca' used. Support will be removed in 1.0")
        config['static_ca'] = True
        config['ca_file'] = globalconfig['server_ca']
    else:
        config['static_ca'] = False
        config['ca_file'] = os.path.join(config['cert_dir'], "{}.ca".format(config['id']))

    # Domain action configuration
    config['actions'] = list()
    for actioncfg in [x for x in localconfig if 'path' in x]:
        config['actions'].append(complete_action_config(actioncfg, config))

    # Domain challenge handler configuration
    config['handlers'] = dict()
    handlerconfigs = [x for x in localconfig if 'mode' in x]
    for domain in config['domainlist']:
        # Use global config as base handler config
        cfg = copy.deepcopy(globalconfig)

        # Determine generic domain handler config values
        genericfgs = [x for x in handlerconfigs if 'domain' not in x]
        if len(genericfgs) > 0:
            cfg.update(genericfgs[0])

        # Update handler config with more specific values (use original names for translated unicode domains)
        _domain = config.get('domaintranslation', {}).get(domain, domain)
        specificcfgs = [x for x in handlerconfigs if 'domain' in x and x['domain'] == _domain]
        if len(specificcfgs) > 0:
            cfg.update(specificcfgs[0])

        config['handlers'][domain] = cfg

    return config


# @brief load the configuration from a file
def load():
    runtimeconfig = dict()
    parser = argparse.ArgumentParser(description="acertmgr - Automated Certificate Manager using ACME/Let's Encrypt")
    parser.add_argument("-c", "--config-file", nargs="?",
                        help="global configuration file (default='{}')".format(DEFAULT_CONF_FILE))
    parser.add_argument("-d", "--config-dir", nargs="?",
                        help="domain configuration directory (default='{}')".format(DEFAULT_CONF_DIR))
    parser.add_argument("-w", "--work-dir", nargs="?",
                        help="persistent work data directory (default=config_dir)")
    parser.add_argument("--authority-tos-agreement", "--tos-agreement", "--tos", nargs="?",
                        help="Agree to the authorities Terms of Service (value required depends on authority)")
    parser.add_argument("--force-renew", "--renew-now", nargs="?",
                        help="Renew all domain configurations matching the given value immediately")
    parser.add_argument("--revoke", nargs="?",
                        help="Revoke a certificate file issued with the currently configured account key.")
    parser.add_argument("--revoke-reason", nargs="?", type=int,
                        help="Provide a revoke reason, see https://tools.ietf.org/html/rfc5280#section-5.3.1")
    args = parser.parse_args()

    # Determine global configuration file
    if args.config_file:
        global_config_file = args.config_file
    elif os.path.isfile(LEGACY_CONF_FILE):
        print("WARNING: Legacy config file '{}' used. Move to '{}' for 1.0".format(LEGACY_CONF_FILE, DEFAULT_CONF_FILE))
        global_config_file = LEGACY_CONF_FILE
    else:
        global_config_file = DEFAULT_CONF_FILE

    # Determine domain configuration directory
    if args.config_dir:
        domain_config_dir = args.config_dir
    elif os.path.isdir(LEGACY_CONF_DIR):
        print("WARNING: Legacy config dir '{}' used. Move to '{}' for 1.0".format(LEGACY_CONF_DIR, DEFAULT_CONF_DIR))
        domain_config_dir = LEGACY_CONF_DIR
    else:
        domain_config_dir = DEFAULT_CONF_DIR

    # Runtime configuration: Get from command-line options
    # - work_dir
    if args.work_dir:
        runtimeconfig['work_dir'] = args.work_dir
    elif os.path.isdir(LEGACY_WORK_DIR) and domain_config_dir == LEGACY_CONF_DIR:
        print("WARNING: Legacy work dir '{}' used. Move to config-dir for 1.0".format(LEGACY_WORK_DIR))
        runtimeconfig['work_dir'] = LEGACY_WORK_DIR
    else:
        runtimeconfig['work_dir'] = domain_config_dir
    #  create work_dir if it does not exist yet
    if not os.path.isdir(runtimeconfig['work_dir']):
        os.mkdir(runtimeconfig['work_dir'], int("0700", 8))

    # - authority_tos_agreement
    if args.authority_tos_agreement:
        runtimeconfig['authority_tos_agreement'] = args.authority_tos_agreement
    elif global_config_file == LEGACY_CONF_FILE:
        # Legacy global config file assumes ToS are agreed
        runtimeconfig['authority_tos_agreement'] = LEGACY_AUTHORITY_TOS_AGREEMENT
    else:
        runtimeconfig['authority_tos_agreement'] = None

    # - force-rewew
    if args.force_renew:
        domaintranslation = idna_convert(args.force_renew.split(' '))
        if len(domaintranslation) > 0:
            runtimeconfig['force_renew'] = ' '.join(domaintranslation.values())
        else:
            runtimeconfig['force_renew'] = args.force_renew

    # - revoke
    if args.revoke:
        runtimeconfig['mode'] = 'revoke'
        runtimeconfig['revoke'] = args.revoke
        runtimeconfig['revoke_reason'] = args.revoke_reason

    # Global configuration: Load from file
    globalconfig = dict()
    if os.path.isfile(global_config_file):
        with io.open(global_config_file) as config_fd:
            try:
                globalconfig = json.load(config_fd)
            except ValueError:
                import yaml
                config_fd.seek(0)
                globalconfig = yaml.safe_load(config_fd)
    if global_config_file == LEGACY_CONF_FILE:
        if 'api' not in globalconfig:
            globalconfig['api'] = LEGACY_API
        if 'authority' not in globalconfig:
            globalconfig['authority'] = LEGACY_AUTHORITY

    # Domain configuration(s): Load from file(s)
    domainconfigs = list()
    if os.path.isdir(domain_config_dir):
        for domain_config_file in os.listdir(domain_config_dir):
            domain_config_file = os.path.join(domain_config_dir, domain_config_file)
            # check file extension and skip if global config file
            if domain_config_file.endswith(".conf") and \
                    os.path.abspath(domain_config_file) != os.path.abspath(global_config_file):
                with io.open(domain_config_file) as config_fd:
                    try:
                        for entry in json.load(config_fd).items():
                            domainconfigs.append(parse_config_entry(entry, globalconfig, runtimeconfig))
                    except ValueError:
                        import yaml
                        config_fd.seek(0)
                        for entry in yaml.safe_load(config_fd).items():
                            domainconfigs.append(parse_config_entry(entry, globalconfig, runtimeconfig))

    return runtimeconfig, domainconfigs
