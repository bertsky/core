from os import environ
from os.path import join, exists
from re import split


def verify_database_url(mongodb_address: str) -> str:
    database_prefix = 'mongodb://'
    if not mongodb_address.startswith(database_prefix):
        error_msg = f'The database address must start with a prefix: {database_prefix}'
        raise ValueError(error_msg)

    address_without_prefix = mongodb_address[len(database_prefix):]
    print(f'Address without prefix: {address_without_prefix}')
    elements = address_without_prefix.split(':', 1)
    if len(elements) != 2:
        raise ValueError('The database address is in wrong format')
    db_host = elements[0]
    db_port = int(elements[1])
    mongodb_url = f'{database_prefix}{db_host}:{db_port}'
    return mongodb_url


def verify_and_parse_rabbitmq_addr(rabbitmq_address: str) -> dict:
    parsed_data = {}
    elements = rabbitmq_address.split('@')
    if len(elements) != 2:
        raise ValueError('The RabbitMQ address is in wrong format. Expected format: username:password@host:port/vhost')

    credentials = elements[0].split(':')
    if len(credentials) != 2:
        raise ValueError(
            'The RabbitMQ credentials are in wrong format. Expected format: username:password@host:port/vhost')

    parsed_data['username'] = credentials[0]
    parsed_data['password'] = credentials[1]

    host_info = split(pattern=r':|/', string=elements[1])
    if len(host_info) != 3 and len(host_info) != 2:
        raise ValueError(
            'The RabbitMQ host info is in wrong format. Expected format: username:password@host:port/vhost')

    parsed_data['host'] = host_info[0]
    parsed_data['port'] = int(host_info[1])
    # The default global vhost is /
    parsed_data['vhost'] = '/' if len(host_info) == 2 else f'/{host_info[2]}'
    return parsed_data


def get_workspaces_dir() -> str:
    """get the path to the workspaces folder

    The processing-workers must have access to the workspaces. First idea is that they are provided
    via nfs and always available under $XDG_DATA_HOME/ocrd-workspaces. This function provides the
    absolute path to the folder and raises a ValueError if it is not available
    """
    if 'XDG_DATA_HOME' in environ:
        xdg_data_home = environ['XDG_DATA_HOME']
    else:
        xdg_data_home = join(environ['HOME'], '.local', 'share')
    res = join(xdg_data_home, 'ocrd-workspaces')
    if not exists(res):
        raise ValueError('Ocrd-Workspaces directory not found. Expected \'{res}\'')
    return res
