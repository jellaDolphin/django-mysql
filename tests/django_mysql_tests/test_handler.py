# -*- coding:utf-8 -*-
from django.db import connection
from django.db.models import F
from django.test import TestCase

from django_mysql_tests.models import Author, AuthorMultiIndex


class HandlerTests(TestCase):
    def setUp(self):
        self.jk = Author.objects.create(name='JK Rowling')
        self.grisham = Author.objects.create(name='John Grisham')

    def test_bad_creation_joins_not_allowed(self):
        qs = Author.objects.filter(tutor__name='A')
        with self.assertRaises(ValueError):
            qs.handler()

    def test_bad_creation_limit_not_allowed(self):
        qs = Author.objects.all()[:100]
        with self.assertRaises(ValueError):
            qs.handler()

    def test_bad_creation_ordering_not_allowed(self):
        qs = Author.objects.order_by('name')
        with self.assertRaises(ValueError):
            qs.handler()

    def test_bad_read_unopened(self):
        handler = Author.objects.all().handler()
        with self.assertRaises(RuntimeError):
            handler.read()

    def test_bad_read_mode(self):
        with Author.objects.handler() as handler:
            with self.assertRaises(ValueError):
                handler.read(mode='non-existent')

    def test_read_does_single_by_default(self):
        with Author.objects.handler() as handler:
            out = list(handler.read())
            self.assertEqual(len(out), 1)
            author = out[0]
            self.assertIn(author, Author.objects.all())

    def test_read_limit_first(self):
        with Author.objects.handler() as handler:
            handler_first = handler.read(limit=1)[0]
        qs_first = Author.objects.earliest('id')

        self.assertEqual(handler_first, qs_first)

    def test_read_limit_last(self):
        with Author.objects.handler() as handler:
            handler_last = handler.read(mode='last', limit=1)[0]
        qs_last = Author.objects.latest('id')

        self.assertEqual(handler_last, qs_last)

    def test_read_limit_all(self):
        with Author.objects.handler() as handler:
            handler_all = list(handler.read(limit=2))
        qs_all = list(Author.objects.all())

        self.assertEqual(handler_all, qs_all)

    def test_read_index_primary(self):
        with Author.objects.handler() as handler:
            handler_all = list(handler.read(index='PRIMARY', limit=2))
        qs_all = list(Author.objects.order_by('id'))

        self.assertEqual(handler_all, qs_all)

    def test_read_index_different(self):
        index_name = [name for name in self.get_index_names(Author)
                      if name != "PRIMARY"][0]

        with Author.objects.handler() as handler:
            handler_all = list(handler.read(index=index_name, limit=2))
        qs_all = list(Author.objects.order_by('name').all())

        self.assertEqual(handler_all, qs_all)

    def test_read_where_filter_read(self):
        qs = Author.objects.filter(name__startswith='John')

        with qs.handler() as handler:
            handler_all = list(handler.read())
        qs_all = list(qs)

        self.assertEqual(handler_all, qs_all)

    def test_read_where_filter_f_expression(self):
        qs = Author.objects.filter(name=F('name'))

        with qs.handler() as handler:
            handler_all = list(handler.read(limit=100))

        self.assertEqual(len(handler_all), 2)

    def test_read_where_exclude(self):
        qs = Author.objects.filter(name__contains='JK')

        with qs.handler() as handler:
            handler_all = list(handler.read())
        qs_all = list(qs)

        self.assertEqual(handler_all, qs_all)

    def test_read_where_filter_params_not_injected_or_modified(self):
        table_col = "`looks_like`.`table_column`"
        author = Author.objects.create(name=table_col)
        qs = Author.objects.filter(name=table_col)

        with qs.handler() as handler:
            handler_first = handler.read()[0]

        self.assertEqual(handler_first, author)

    def test_read_where_passed_in(self):
        qs = Author.objects.filter(name__startswith='John')

        with Author.objects.handler() as handler:
            handler_author = handler.read(where=qs)[0]

        self.assertEqual(handler_author, qs[0])

    def test_read_where_passed_in_overrides_completely(self):
        qs = Author.objects.filter(name='JK Rowling')
        qs2 = Author.objects.filter(name='John Grisham')

        with qs.handler() as handler:
            handler_default = handler.read()[0]
            handler_where = handler.read(where=qs2)[0]

        self.assertEqual(handler_default, qs[0])
        self.assertEqual(handler_where, qs2[0])

    def test_read_bad_where_passed_in(self):
        with Author.objects.handler() as handler:
            with self.assertRaises(ValueError):
                handler.read(where=Author.objects.filter(tutor__name='A'))

    def test_read_index_value_and_mode_invalid(self):
        with Author.objects.handler() as handler:
            with self.assertRaises(ValueError):
                handler.read(index_value=1, mode='first')

    def test_read_index_pk(self):
        with Author.objects.handler() as handler:
            handler_result = handler.read(index_value=self.jk.id)[0]
        self.assertEqual(handler_result, self.jk)

    def test_read_index_multipart(self):
        AuthorMultiIndex.objects.create(name='John Smith', country='Scotland')
        smith2 = AuthorMultiIndex.objects.create(name='John Smith',
                                                 country='England')

        idx_name = [name for name in self.get_index_names(AuthorMultiIndex)
                    if name != "PRIMARY"][0]

        with AuthorMultiIndex.objects.handler() as handler:
            value = ('John Smith', 'England')
            handler_result = handler.read(index=idx_name, index_value=value)[0]

        self.assertEqual(handler_result, smith2)

    def test_iter_all(self):
        all_ids = list(Author.objects.values_list('id', flat=True))

        with Author.objects.handler() as handler:
            seen_ids = [author.id for author in handler.iter()]

        self.assertEqual(seen_ids, list(sorted(all_ids)))

    def test_iter_chunk_size_1(self):
        all_ids = list(Author.objects.values_list('id', flat=True))

        with Author.objects.handler() as handler:
            seen_ids = [author.id for author in handler.iter()]

        self.assertEqual(seen_ids, list(sorted(all_ids)))

    def test_iter_reverse(self):
        all_ids = list(Author.objects.values_list('id', flat=True))

        with Author.objects.handler() as handler:
            seen_ids = [author.id for author in handler.iter(forwards=False)]

        self.assertEqual(seen_ids, list(sorted(all_ids, reverse=True)))

    def test_bad_iter_unopened(self):
        handler = Author.objects.all().handler()
        with self.assertRaises(RuntimeError):
            sum(1 for x in handler.iter())

    @classmethod
    def get_index_names(cls, model):
        # There's no easy way of getting index names in django so get the name
        # of the index on Author.name from INFORMATION_SCHEMA
        with connection.cursor() as cursor:
            cursor.execute(
                """SELECT DISTINCT INDEX_NAME
                FROM INFORMATION_SCHEMA.STATISTICS
                WHERE TABLE_SCHEMA = %s AND
                      TABLE_NAME = %s""",
                (connection.settings_dict['NAME'], model._meta.db_table)
            )

            index_names = [x[0] for x in cursor.fetchall()]
        return index_names
