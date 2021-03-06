from logging import getLogger

from Missing import MV
from Acquisition import aq_parent, aq_base
from DateTime import DateTime
from Products.PluginIndexes.common import safe_callable
from Products.PluginIndexes.KeywordIndex.KeywordIndex import KeywordIndex
from Products.PluginIndexes.FieldIndex.FieldIndex import FieldIndex
from Products.PluginIndexes.DateIndex.DateIndex import DateIndex
from Products.ZCTextIndex.ZCTextIndex import ZCTextIndex
from Products.PluginIndexes.BooleanIndex.BooleanIndex import BooleanIndex
from Products.PluginIndexes.UUIDIndex.UUIDIndex import UUIDIndex
from Products.ExtendedPathIndex.ExtendedPathIndex import ExtendedPathIndex
from Products.PluginIndexes.DateRangeIndex.DateRangeIndex import DateRangeIndex

from plone.app.folder.nogopip import GopipIndex

from pyes import (PrefixFilter, TermFilter, ORFilter, RangeFilter,
                  ANDFilter, TextQuery, ESRangeOp)

logger = getLogger(__name__)
info = logger.info


class BaseIndex(object):

    def __init__(self, catalog, index):
        self.catalog = catalog
        self.index = index

    def create_mapping(self, name):
        return {
            'type': 'string',
            'index': 'not_analyzed',
            'store': False
        }

    def get_value(self, object):
        value = None
        attrs = self.index.getIndexSourceNames()
        if len(attrs) > 0:
            attr = attrs[0]
        else:
            attr = ''

        if hasattr(self.index, 'index_object'):
            value = self.index._get_object_datum(object, attr)
        else:
            info('catalogObject was passed bad index '
                      'object %s.' % str(self.index))
        if value == MV:
            return None
        return value

    def extract(self, name, data):
        return data[name] or ''

    def _normalize_query(self, query):
        if isinstance(query, dict) and 'query' in query:
            return query['query']
        return query

    def get_query(self, name, value):
        value = self._normalize_query(value)
        if value in (None, ''):
            return
        elif type(value) in (list, tuple, set):
            if len(value) == 0:
                return
            queries = []
            for val in value:
                queries.append(TermFilter(name, val))
            return ORFilter(queries)
        else:
            return TermFilter(name, value)


class EKeywordIndex(BaseIndex):
    def extract(self, name, data):
        return data[name] or []


class EFieldIndex(BaseIndex):
    pass


class EDateIndex(BaseIndex):

    # XXX elastic search requires default
    # value for searching. This could be a problem...
    missing_date = DateTime('1900/01/01')

    def create_mapping(self, name):
        return {
            'type': 'date',
            'store': False
        }

    def get_value(self, object):
        value = super(EDateIndex, self).get_value(object)
        if type(value) == list:
            if len(value) == 0:
                value = None
            else:
                value = value[0]
        if value in ('None', MV, None, ''):
            value = self.missing_date

        if isinstance(value, basestring):
            return DateTime(value).ISO8601()
        elif isinstance(value, DateTime):
            return value.ISO8601()
        return value

    def get_query(self, name, value):
        range_ = value.get('range')
        query = value.get('query')
        if query is None:
            return
        if range_ is None:
            if type(query) in (list, tuple):
                range_ = 'min'
        
        if range_ == 'min':
            return RangeFilter(ESRangeOp(name, 'lte', query.ISO8601()))
        elif range_ == 'max':
            return RangeFilter(ESRangeOp(name, 'gte', query.ISO8601()))
        elif range_ == 'min:max' and type(query) in (list, tuple) and \
                len(query) == 2:
            return ANDFilter([
                RangeFilter(ESRangeOp(name, 'gte', query[0].ISO8601())),
                RangeFilter(ESRangeOp(name, 'lte', query[1].ISO8601()))
                ])

    def extract(self, name, data):
        try:
            return DateTime(super(EDateIndex, self).extract(name, data))
        except:
            return None


class EZCTextIndex(BaseIndex):
    
    def create_mapping(self, name):
        return {
            'type': 'string',
            'index': 'analyzed',
            'store': False
        }

    def get_value(self, object):
        try:
            fields = self.index._indexed_attrs
        except:
            fields  = [ self.index._fieldname ]

        all_texts = []
        for attr in fields:
            text = getattr(object, attr, None)
            if text is None:
                continue
            if safe_callable(text):
                text = text()
            if text is None:
                continue
            if text:
                if isinstance(text, (list, tuple, )):
                    all_texts.extend(text)
                else:
                    all_texts.append(text)

        # Check that we're sending only strings
        all_texts = filter(lambda text: isinstance(text, basestring), \
                           all_texts)
        if all_texts:
            return '\n'.join(all_texts)

    def get_query(self, name, value):
        value = self._normalize_query(value)
        return TextQuery(name, value), True


class EBooleanIndex(BaseIndex):

    def create_mapping(self, name):
        return {'type': 'boolean'}


class EUUIDIndex(BaseIndex):
    pass


class EExtendedPathIndex(BaseIndex):

    def create_mapping(self, name):
        return {
            'properties': {
                'path': {
                    'type': 'string',
                    'index': 'analyzed',
                    'index_analyzer': 'keyword',
                    'store': False
                },
                'depth': {
                    'type': 'integer',
                    'store': False
                }
            }
        }

    def get_value(self, object):
        attrs = self.index.indexed_attrs
        index = attrs is None and self.index.id or attrs[0]

        path = getattr(object, index, None)
        if path is not None:
            if safe_callable(path):
                path = path()

            if not isinstance(path, (str, tuple)):
                raise TypeError('path value must be string or tuple '
                                'of strings: (%r, %s)' % (index, repr(path)))
        else:
            try:
                path = object.getPhysicalPath()
            except AttributeError:
                return
        return {
            'path': '/'.join(path),
            'depth': len(path) - 1
        }

    def extract(self, name, data):
        return data[name]['path']

    def get_query(self, name, value):
        if isinstance(value, basestring):
            paths = value
            depth = -1
            navtree = False
            navtree_start = 0
        else:
            depth = value.get('depth', -1)
            paths = value.get('query')
            navtree = value.get('navtree', False)
            navtree_start = value.get('navtree_start', 0)
        if not paths:
            return
        if isinstance(paths, basestring):
            paths = [paths]
        andfilters = []
        for path in paths:
            spath = path.split('/')
            gtcompare = 'gt'
            start = len(spath) - 1

            if navtree:
                start = start + navtree_start
                end = navtree_start + depth
            else:                
                end = start + depth
            if navtree or depth == -1:
                gtcompare = 'gte'
            
            filters = []
            if depth == 0:
                andfilters.append(TermFilter(name + '.path', path))
                continue
            else:
                filters = [
                    PrefixFilter(name + '.path', path),
                    RangeFilter(ESRangeOp(name + '.depth', gtcompare, start))]
            if depth != -1:
                filters.append(
                    RangeFilter(ESRangeOp(name + '.depth', 'lte', end)))
            andfilters.append(ANDFilter(filters))
        if len(andfilters) > 1:
            return ORFilter(andfilters)
        else:
            return andfilters[0]


class EGopipIndex(BaseIndex):
    
    def create_mapping(self, name):
        return {
            'type': 'integer',
            'store': False
        }

    def get_value(self, object):
        parent = aq_parent(object)
        if hasattr(parent, 'getObjectPosition'):
            return parent.getObjectPosition(object.getId())


class EDateRangeIndex(BaseIndex):

    def create_mapping(self, name):
        return {
            'properties': {
                '%s1' % name: {
                    'type': 'date',
                    'store': False
                },
                '%s2' % name: {
                    'type': 'date',
                    'store': False
                }
            }
        }

    def get_value(self, object):
        if self.index._since_field is None:
            return

        since = getattr(object, self.index._since_field, None)
        if safe_callable(since):
            since = since()

        until = getattr(object, self.index._until_field, None)
        if safe_callable(until):
            until = until()
        if not since or not until:
            return

        return {
            '%s1' % self.index.id: since.ISO8601(),
            '%s2' % self.index.id: until.ISO8601()}

    def get_query(self, name, value):
        value = self._normalize_query(value)
        date = value.ISO8601()
        return ANDFilter([
            RangeFilter(ESRangeOp('%s.%s1' % (name, name), 'lte', date)),
            RangeFilter(ESRangeOp('%s.%s2' % (name, name), 'gte', date))
            ])


INDEX_MAPPING = {
    KeywordIndex: EKeywordIndex,
    FieldIndex: EFieldIndex,
    DateIndex: EDateIndex,
    ZCTextIndex: EZCTextIndex,
    BooleanIndex: EBooleanIndex,
    UUIDIndex: EUUIDIndex,
    ExtendedPathIndex: EExtendedPathIndex,
    GopipIndex: EGopipIndex,
    DateRangeIndex: EDateRangeIndex
}


def getIndex(catalog, name):
    try:
        index = aq_base(catalog.getIndex(name))
    except KeyError:
        return
    index_type = type(index)
    if index_type in INDEX_MAPPING:
        return INDEX_MAPPING[index_type](catalog, index)
