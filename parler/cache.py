"""
django-parler uses caching to avoid fetching model data when it doesn't have to.

These functions are used internally by django-parler to fetch model data.
Since all calls to the translation table are routed through our model descriptor fields,
cache access and expiry is rather simple to implement.
"""
from django.core.cache import cache
from django.utils import six
from parler import appsettings
from parler.utils import get_language_settings

if six.PY3:
    long = int

try:
    # In Django 1.6, a timeout of 0 seconds is accepted as valid input,
    # and a sentinel value is used to denote the default timeout. Use that.
    from django.core.cache.backends.base import DEFAULT_TIMEOUT
except ImportError:
    DEFAULT_TIMEOUT = 0


class IsMissing(object):
    # Allow _get_any_translated_model() to evaluate this as False.
    def __nonzero__(self):
        return False   # Python 2
    def __bool__(self):
        return False   # Python 3
    def __repr__(self):
        return "<IsMissing>"

MISSING = IsMissing()  # sentinel value


def get_object_cache_keys(instance):
    """
    Return the cache keys associated with an object.
    """
    if not instance.pk or instance._state.adding:
        return []

    keys = []
    # TODO: performs a query to fetch the language codes. Store that in memcached too.
    for language in instance.get_available_languages():
        keys.append(get_translation_cache_key(instance._translations_model, instance.pk, language))

    return keys


def get_translation_cache_key(translated_model, master_id, language_code):
    """
    The low-level function to get the cache key for a translation.
    """
    # Always cache the entire object, as this already produces
    # a lot of queries. Don't go for caching individual fields.
    return 'parler.{0}.{1}.{2}.{3}'.format(translated_model._meta.app_label, translated_model.__name__, long(master_id), language_code)


def get_cached_translation(instance, language_code, use_fallback=False):
    """
    Fetch an cached translation.
    """
    values = _get_cached_values(instance, language_code, use_fallback)
    if not values:
        return None

    translation = instance._translations_model(**values)
    translation._state.adding = False
    return translation


def get_cached_translated_field(instance, language_code, field_name, use_fallback=False):
    """
    Fetch an cached field.
    """
    values = _get_cached_values(instance, language_code, use_fallback)
    if not values:
        return None

    # Allow older cached versions where the field didn't exist yet.
    return values.get(field_name, None)


def _get_cached_values(instance, language_code, use_fallback=False):
    """
    Fetch an cached field.
    """
    if not appsettings.PARLER_ENABLE_CACHING or not instance.pk or instance._state.adding:
        return None

    key = get_translation_cache_key(instance._translations_model, instance.pk, language_code)
    values = cache.get(key)
    if not values:
        return None

    # Check for a stored fallback marker
    if values.get('__FALLBACK__', False):
        # Internal trick, already set the fallback marker, so no query will be performed.
        instance._translations_cache[language_code] = MISSING

        # Allow to return the fallback language instead.
        if use_fallback:
            lang_dict = get_language_settings(language_code)
            if lang_dict['fallback'] != language_code:
                return _get_cached_values(instance, lang_dict['fallback'], use_fallback=False)
        return None

    values['master'] = instance
    values['language_code'] = language_code
    return values


def _cache_translation(translation, timeout=DEFAULT_TIMEOUT):
    """
    Store a new translation in the cache.
    """
    if not appsettings.PARLER_ENABLE_CACHING:
        return

    # Cache a translation object.
    # For internal usage, object parameters are not suited for outside usage.
    fields = translation.get_translated_fields()
    values = {'id': translation.id}
    for name in fields:
        values[name] = getattr(translation, name)

    key = get_translation_cache_key(translation.__class__, translation.master_id, translation.language_code)
    cache.set(key, values, timeout=timeout)



def _cache_translation_needs_fallback(instance, language_code, timeout=DEFAULT_TIMEOUT):
    """
    Store the fact that a translation doesn't exist, and the fallback should be used.
    """
    if not appsettings.PARLER_ENABLE_CACHING or not instance.pk or instance._state.adding:
        return

    key = get_translation_cache_key(instance._translations_model, instance.pk, language_code)
    cache.set(key, {'__FALLBACK__': True}, timeout=timeout)


def _delete_cached_translations(shared_model):
    for key in get_object_cache_keys(shared_model):
        cache.delete(key)


def _delete_cached_translation(translation):
    if not appsettings.PARLER_ENABLE_CACHING:
        return

    # Delete a cached translation
    # For internal usage, object parameters are not suited for outside usage.
    key = get_translation_cache_key(translation.__class__, translation.master_id, translation.language_code)
    cache.delete(key)
