from django.template.loader import get_template

from . import registry


@registry.function
def recaptcha_init(language=None):
    return get_template('snowpenguin/recaptcha/recaptcha_init.html').render({'explicit': False, 'language': language})
