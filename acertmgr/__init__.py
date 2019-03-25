#!/usr/bin/env python
# -*- coding: utf-8 -*-

# Automated Certificate Manager using ACME
# Copyright (c) Markus Hauschild & David Klaftenegger, 2016.
# Copyright (c) Rudolf Mayerhofer, 2019.
# available under the ISC license, see LICENSE

import grp
import importlib
import io
import os
import pwd
import stat
import subprocess

from acertmgr import configuration, tools


# @brief create a authority for the given configuration
# @param settings the authority configuration options
def create_authority(settings):
    acc_file = settings['account_key']
    if os.path.isfile(acc_file):
        print("Reading account key from {}".format(acc_file))
        acc_key = tools.read_pem_file(acc_file, key=True)
    else:
        print("Account key not found at '{0}'. Creating key.".format(acc_file))
        acc_key = tools.new_account_key(acc_file)

    authority_module = importlib.import_module("acertmgr.authority.{0}".format(settings["api"]))
    authority_class = getattr(authority_module, "ACMEAuthority")
    return authority_class(settings, acc_key)


# @brief create a challenge handler for the given configuration
# @param settings the domain's configuration options
def create_challenge_handler(settings):
    if "mode" in settings:
        mode = settings["mode"]
    else:
        mode = "standalone"

    handler_module = importlib.import_module("acertmgr.modes.{0}".format(mode))
    handler_class = getattr(handler_module, "ChallengeHandler")
    return handler_class(settings)


# @brief fetch new certificate from letsencrypt
# @param settings the domain's configuration options
def cert_get(settings):
    print("Getting certificate for '%s'." % settings['domains'])

    acme = create_authority(settings)
    acme.register_account()

    # create challenge handlers for this certificate
    challenge_handlers = dict()
    for domain in settings['domainlist']:
        # Create the challenge handler
        challenge_handlers[domain] = create_challenge_handler(settings['handlers'][domain])

    # create ssl key
    key_file = settings['key_file']
    key_length = settings['key_length']
    if os.path.isfile(key_file):
        key = tools.read_pem_file(key_file, key=True)
    else:
        print("SSL key not found at '{0}'. Creating {1} bit key.".format(key_file, key_length))
        key = tools.new_ssl_key(key_file, key_length)

    # create ssl csr
    csr_file = settings['csr_file']
    if os.path.isfile(csr_file) and str(settings['csr_static']).lower() == 'true':
        print('Loading CSR from {}'.format(csr_file))
        cr = tools.read_pem_file(csr_file, csr=True)
    else:
        print('Generating CSR for {}'.format(settings['domainlist']))
        cr = tools.new_cert_request(settings['domainlist'], key)
        tools.write_pem_file(cr, csr_file)

    # request cert with csr
    crt, ca = acme.get_crt_from_csr(cr, settings['domainlist'], challenge_handlers)

    #  if resulting certificate is valid: store in final location
    if tools.is_cert_valid(crt, settings['ttl_days']):
        tools.write_pem_file(crt, settings['cert_file'], stat.S_IREAD)
        if "static_ca" in settings and not settings['static_ca'] and ca is not None:
            tools.write_pem_file(ca, settings['ca_file'])


# @brief put new certificate in place
# @param settings the domain's configuration options
# @return the action to be executed after the certificate update
def cert_put(settings):
    # TODO error handling
    ca_file = settings['ca_file']
    crt_user = settings['user']
    crt_group = settings['group']
    crt_perm = settings['perm']
    crt_path = settings['path']
    crt_format = settings['format'].split(",")
    crt_format = [str.strip(x) for x in crt_format]
    crt_action = settings['action']

    key_file = settings['key_file']
    crt_final = settings['cert_file']

    with io.open(crt_path, "w+") as crt_fd:
        for fmt in crt_format:
            if fmt == "crt":
                src_fd = io.open(crt_final, "r")
                crt_fd.write(src_fd.read())
                src_fd.close()
            if fmt == "key":
                src_fd = io.open(key_file, "r")
                crt_fd.write(src_fd.read())
                src_fd.close()
            if fmt == "ca":
                if not os.path.isfile(ca_file):
                    raise FileNotFoundError("The CA certificate file (%s) is missing!" % ca_file)
                src_fd = io.open(ca_file, "r")
                crt_fd.write(src_fd.read())
                src_fd.close()
            else:
                # TODO error handling
                pass

    # set owner and permissions
    uid = pwd.getpwnam(crt_user).pw_uid
    gid = grp.getgrnam(crt_group).gr_gid
    try:
        os.chown(crt_path, uid, gid)
    except OSError:
        print('Warning: Could not set certificate file ownership!')
    try:
        os.chmod(crt_path, int(crt_perm, 8))
    except OSError:
        print('Warning: Could not set certificate file permissions!')

    return crt_action


def main():
    # load config
    configs = configuration.load()

    # post-update actions (run only once)
    actions = set()

    # check certificate validity and obtain/renew certificates if needed
    for config in configs:
        cert_file = config['cert_file']
        cert_file_exists = os.path.isfile(cert_file)
        if cert_file_exists:
            cert = tools.read_pem_file(cert_file)
        if not cert_file_exists or not tools.is_cert_valid(cert, config['ttl_days']):
                cert_get(config)

        for cfg in config['actions']:
            if not tools.target_is_current(cfg['path'], cert_file):
                print("Updating '{}' due to newer version".format(cfg['path']))
                actions.add(cert_put(cfg))

    # run post-update actions
    for action in actions:
        if action is not None:
            try:
                # Run actions in a shell environment (to allow shell syntax) as stated in the configuration
                output = subprocess.check_output(action, shell=True, stderr=subprocess.STDOUT)
                print("Executed '{}' successfully: {}".format(action, output))
            except subprocess.CalledProcessError as e:
                print("Execution of '{}' failed with error '{}': {}".format(e.cmd, e.returncode, e.output))
