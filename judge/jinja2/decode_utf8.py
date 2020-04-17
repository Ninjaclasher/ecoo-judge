from . import registry


@registry.filter
def decode_utf8(val):
    return str(val, 'utf8')
