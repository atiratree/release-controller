#!/usr/bin/python3

import argparse
import json
import logging
import time

import openshift as oc
from openshift import OpenShiftPythonException

logging.basicConfig(level=logging.INFO, format='[%(asctime)s] %(message)s')
logger = logging.getLogger('releaseTool')

SUPPORTED_ARCHITECTURES = ['amd64', 'arm64', 'ppc64le', 's390x', 'multi']


def generate_resource_values(ns, name, architecture, private):
    arch_suffix, private_suffix = "", ""

    if architecture != 'amd64':
        arch_suffix = f'-{architecture}'

    if private:
        private_suffix = '-priv'

    return f'{ns}{arch_suffix}{private_suffix}', f'{name}{arch_suffix}{private_suffix}'


def validate_server_connection(ctx):
    with oc.options(ctx), oc.tracking(), oc.timeout(60):
        try:
            username = oc.whoami()
            version = oc.get_server_version()
            logger.debug(f'Connected to APIServer running version: {version}, as: {username}')
        except (ValueError, OpenShiftPythonException, Exception) as e:
            logger.error(f"Unable to verify cluster connection using context: \"{ctx['context']}\"")
            raise e


def update(ctx, action, ns, name, release, custom_message, custom_reason, execute):
    with oc.options(ctx), oc.tracking(), oc.timeout(5*60):
        try:
            with oc.project(ns):
                tag = oc.selector(f'imagestreamtag/{name}:{release}').object(ignore_not_found=True)
                if not tag:
                    raise Exception(f'Unable to locate imagestreamtag: {ns}/{name}:{release}')

                ts = int(round(time.time() * 1000))
                backup_filename = f'{name}_{release}-{ts}.json'
                if execute:
                    with open(backup_filename, mode='w+', encoding='utf-8') as backup:
                        logger.debug(f'Creating backup file: {backup_filename}')
                        backup.write(json.dumps(tag.model._primitive(), indent=4))

                def update_annotations(obj):
                    message = f'Manually {action}ed per TRT'
                    if custom_message is not None:
                        message = custom_message

                    for annotations in (obj.model.image.metadata.annotations, obj.model.metadata.annotations, obj.model.tag.annotations):
                        if action == 'accept':
                            annotations['release.openshift.io/phase'] = 'Accepted'
                        elif action == 'reject':
                            annotations['release.openshift.io/phase'] = 'Rejected'
                        else:
                            raise ValueError(f'Unsupported action specified: {action}')

                        annotations['release.openshift.io/message'] = message

                        if custom_reason is not None:
                            annotations['release.openshift.io/reason'] = custom_reason

                    logger.info(f'{action.capitalize()}ing: {ns}/{name}:{release}')
                    if execute:
                        logger.debug(f'Updated payload:\n{json.dumps(obj.model._primitive(), indent=4)}')
                        return True
                    else:
                        logger.info(f'Updated payload:\n{json.dumps(obj.model._primitive(), indent=4)}')
                        logger.warning('You must specify "--execute" to permanently apply these changes')
                        exit(0)

                update_annotations(tag)
                tag.replace()
                logger.info(f'Release {release} updated successfully')
                logger.info(f'Backup written to: {backup_filename}')

        except (ValueError, OpenShiftPythonException, Exception) as e:
            logger.error(f'Unable to update release: "{release}"')
            raise e


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Manually accept or reject release payloads')
    parser.add_argument('-m', '--message', help='Specifies a custom message to include with the update', default=None)
    parser.add_argument('-r', '--reason', help='Specifies a custom reason to include with the update', default=None)
    parser.add_argument('--execute', help='Specify to persist changes on the cluster', action='store_true')
    parser.add_argument('--admin', help='Perform operations as "system:admin"', action='store_true')

    config_group = parser.add_argument_group('Configuration Options')
    config_group.add_argument('-v', '--verbose', help='Enable verbose output', action='store_true')

    ocp_group = parser.add_argument_group('Openshift Configuration Options')
    ocp_group.add_argument('-c', '--context', help='The OC context to use (default is "app.ci")', default='app.ci')
    ocp_group.add_argument('-n', '--namespace', help='The namespace prefix to use (default is "ocp")', default='ocp')
    ocp_group.add_argument('-i', '--imagestream', help='The name of the release imagestream to use (default is "release")', default='release')
    ocp_group.add_argument('-a', '--architecture', help='The architecture of the release to process (default is "amd64")', choices=SUPPORTED_ARCHITECTURES, default='amd64')
    ocp_group.add_argument('-p', '--private', help='Enable updates of "private" releases', action='store_true')

    subparsers = parser.add_subparsers(title='subcommands', description='valid subcommands', help='Supported operations', required=True)
    accept_parser = subparsers.add_parser('accept', help='Accepts the specified release')
    accept_parser.set_defaults(action='accept')
    reject_parser = subparsers.add_parser('reject', help='Rejects the specified release')
    reject_parser.set_defaults(action='reject')

    parser.add_argument('release', help='The name of the release to process (i.e. 4.10.0-0.ci-2021-12-17-144800)')

    args = vars(parser.parse_args())

    if args['verbose']:
        logger.setLevel(logging.DEBUG)

    context = {"context": args['context']}

    if args['admin']:
        context['as'] = 'system:admin'

    validate_server_connection(context)
    namespace, imagestream = generate_resource_values(args['namespace'], args['imagestream'], args['architecture'], args['private'])
    update(context, args['action'], namespace, imagestream, args['release'], args['message'], args['reason'], args['execute'])
