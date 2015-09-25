# -*- coding:utf-8 -*-
from __future__ import absolute_import, unicode_literals

import json

import django
from django.core import checks
from django.db import connections
from django.db.models import Field, Transform
from django.utils import six

from django_mysql import forms
from django_mysql.compat import field_class

__all__ = ('JSONField',)


class JSONField(field_class(Field)):
    def __init__(self, *args, **kwargs):
        if 'default' not in kwargs:
            kwargs['default'] = dict
        super(JSONField, self).__init__(*args, **kwargs)

    def check(self, **kwargs):
        errors = super(JSONField, self).check(**kwargs)
        errors.extend(self._check_django_version())

        if django.VERSION[:2] >= (1, 8):
            # This check connects to the DB, which only works on Django 1.8+
            errors.extend(self._check_mysql_version())

        return errors

    def _check_django_version(self):
        errors = []
        if django.VERSION[:2] < (1, 8):
            errors.append(
                checks.Error(
                    "Django 1.8+ is required to use JSONField",
                    obj=self,
                    id='django_mysql.E015',
                )
            )
        return errors

    def _check_mysql_version(self):
        errors = []

        any_conn_works = False
        conn_names = ['default'] + list(set(connections) - {'default'})
        for db in conn_names:
            if (
                not connections[db].is_mariadb and
                connections[db].mysql_version >= (5, 7)
            ):
                any_conn_works = True

        if not any_conn_works:
            errors.append(
                checks.Error(
                    "MySQL 5.7+ is required to use JSONField",
                    hint=None,
                    obj=self,
                    id='django_mysql.E016'
                )
            )
        return errors

    def deconstruct(self):
        name, path, args, kwargs = super(JSONField, self).deconstruct()
        path = 'django_mysql.models.%s' % self.__class__.__name__
        return name, path, args, kwargs

    def db_type(self, connection):
        return 'json'

    def get_transform(self, name):
        transform = super(JSONField, self).get_transform(name)
        if transform:
            return transform  # pragma: no cover
        return KeyTransformFactory(name)

    def from_db_value(self, value, expression, connection, context):
        # Similar to to_python, for Django 1.8+
        if isinstance(value, six.string_types):
            return json.loads(value)
        return value

    def get_prep_value(self, value):
        if value is not None and not isinstance(value, six.string_types):
            # For some reason this value gets string quoted in Django's SQL
            # compiler...
            return json.dumps(value)
        return value

    def get_prep_lookup(self, lookup_type, value):
        if (
            not hasattr(value, '_prepare') and
            lookup_type in ('exact', 'gt', 'gte', 'lt', 'lte') and
            value is not None
        ):
            return JSONValue(value)

        return super(JSONField, self).get_prep_lookup(lookup_type, value)

    def get_lookup(self, lookup_name):
        # Have to 'unregister' some incompatible lookups
        if lookup_name in {
            'range', 'in', 'iexact', 'contains', 'icontains', 'startswith',
            'istartswith', 'endswith', 'iendswith', 'search', 'regex', 'iregex'
        }:
            raise NotImplementedError(
                "Lookup '{}' doesn't work with JSONField".format(lookup_name)
            )
        return super(JSONField, self).get_lookup(lookup_name)

    def formfield(self, **kwargs):
        defaults = {'form_class': forms.JSONField}
        defaults.update(kwargs)
        return super(JSONField, self).formfield(**defaults)


class JSONValue(object):
    def __init__(self, value):
        self.json_string = json.dumps(value)

    def as_sql(self, *args, **kwargs):
        return 'CAST(%s AS JSON)', (self.json_string,)


class KeyTransform(Transform):

    def __init__(self, key_name, *args, **kwargs):
        super(KeyTransform, self).__init__(*args, **kwargs)
        self.key_name = key_name

    def as_sql(self, compiler, connection):
        key_transforms = [self.key_name]
        previous = self.lhs
        while isinstance(previous, KeyTransform):
            key_transforms.insert(0, previous.key_name)
            previous = previous.lhs

        lhs, params = compiler.compile(previous)

        json_path = self.compile_json_path(key_transforms)

        return 'JSON_EXTRACT({}, %s)'.format(lhs), params + [json_path]

    def compile_json_path(self, key_transforms):
        path = ['$']
        for key_transform in key_transforms:
            try:
                num = int(key_transform)
                path.append('[{}]'.format(num))
            except ValueError:  # non-integer
                path.append('.')
                path.append(key_transform)
        return ''.join(path)


class KeyTransformFactory(object):

    def __init__(self, key_name):
        self.key_name = key_name

    def __call__(self, *args, **kwargs):
        return KeyTransform(self.key_name, *args, **kwargs)
