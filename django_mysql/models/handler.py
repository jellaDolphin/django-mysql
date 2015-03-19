from random import randint
import re

from django.db import connections


class Handler(object):

    def __init__(self, queryset):
        self.open = False

        self.db = queryset.db
        self._model = queryset.model
        self._table_name = self._model._meta.db_table
        self._handler_name = '{}_{}'.format(self._table_name, randint(1, 2e10))

        self._set_where(queryset)

    def _set_where(self, queryset):
        """
        Was this a queryset with filters/excludes/expressions set? If so,
        extract the WHERE clause from the ORM output so we can use it in the
        handler queries
        """
        if not self._is_simple_query(queryset.query):
            raise ValueError("This QuerySet's WHERE clause is too complex to "
                             "be used in a HANDLER")

        sql, params = queryset.query.sql_with_params()
        where_pos = sql.find('WHERE ')
        if where_pos != -1:
            # Cut the query to extract just its WHERE clause
            where_clause = sql[where_pos:]
            # Replace absolute table.column references with relative ones
            # since that is all HANDLER can work with
            # This is a bit flakey - if you inserted extra SQL with extra() or
            # an expression or something it might break.
            where_clause, _ = self.absolute_col_re.subn(r"\1", where_clause)
            self._where_clause = where_clause
            self._params = params
        else:
            self._where_clause = ""
            self._params = ()

    # For modifying the queryset SQL. Attempts to match the TABLE.COLUMN
    # pattern that Django compiles. Clearly not perfect.
    absolute_col_re = re.compile("`[^`]+`.(`[^`]+`)")

    @classmethod
    def _is_simple_query(cls, query):
        """
        Inspect the internals of the Query and say if we think its WHERE clause
        can be used in a HANDLER statement
        """
        return (
            not query.low_mark and
            not query.high_mark and
            not query.select and
            not query.group_by and
            not query.having and
            not query.distinct and
            query.default_ordering and
            len(query.tables) <= 1
        )

    # Context manager

    def __enter__(self):
        self.cursor = connections[self.db].cursor()
        self.cursor.__enter__()
        self.cursor.execute("HANDLER `{}` OPEN AS {}"
                            .format(self._table_name, self._handler_name))
        self.open = True
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.cursor.execute("HANDLER `{}` CLOSE".format(self._handler_name))
        self.cursor.__exit__(exc_type, exc_value, traceback)
        self.open = False

    # Public methods

    def read(self, mode='first', limit=None):
        if not self.open:
            raise RuntimeError("This handler isn't open yet")

        sql = ["HANDLER {} READ `PRIMARY`".format(self._handler_name)]
        params = ()

        if mode == 'first':
            sql.append("FIRST")
        elif mode == 'last':
            sql.append("LAST")
        elif mode == 'next':
            sql.append("NEXT")
        elif mode == 'prev':
            sql.append("PREV")
        else:
            raise ValueError("'mode' must be one of: first, last, next, prev")

        if self._where_clause:
            sql.append(self._where_clause)
            params += self._params

        if limit is not None:
            sql.append("LIMIT %s")
            params += (limit,)

        return self._model.objects.raw(" ".join(sql), params)

    def iter(self, chunk_size=100, forwards=True):
        if forwards:
            mode = 'first'
        else:
            mode = 'last'

        while True:
            count = 0
            for obj in self.read(mode=mode, limit=chunk_size):
                count += 1
                yield obj

            if count < chunk_size:
                return

            if forwards:
                mode = 'next'
            else:
                mode = 'prev'
