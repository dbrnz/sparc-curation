import json
from collections import deque
import rdflib
import ontquery as oq
from joblib import Memory
from pysercomb.pyr.units import ProtcParameter
from pyontutils.core import OntId
from pyontutils.utils import makeSimpleLogger
from sparcur import exceptions as exc
from sparcur.config import config

log = makeSimpleLogger('sparcur')
sparc = rdflib.Namespace('http://uri.interlex.org/tgbugs/uris/readable/sparc/')

# disk cache decorator
memory = Memory(config.cache_dir, verbose=0)


def lj(j):
    """ use with log to format json """
    return '\n' + json.dumps(j, indent=2)


class _log:
    """ logging prevents nice ipython recurions error printing
        so rename this class to log when you need fake logging """
    @staticmethod
    def debug(nothing): pass
    @staticmethod
    def info(nothing): pass
    @staticmethod
    def warning(nothing): print(nothing)
    @staticmethod
    def error(nothing): pass
    @staticmethod
    def critical(nothing): pass


class JEncode(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, tuple):
            return list(obj)
        elif isinstance(obj, deque):
            return list(obj)
        elif isinstance(obj, ProtcParameter):
            return str(obj)
        elif isinstance(obj, OntId):
            return obj.curie + ',' + obj.label

        # Let the base class default method raise the TypeError
        return json.JSONEncoder.default(self, obj)


def python_identifier(string):
    """ pythonify a string for use as an identifier """
    ident = (string.strip()
             .replace('(', '')
             .replace(')', '')
             .replace(' ', '_')
             .replace('+', '')
             .replace('…','')
             .replace('.','_')
             .replace(',','_')
             .replace('/', '_')
             .replace('?', '_')
             .replace('#', 'number')
             .replace('-', '_')
             .replace(':', '_')
             .replace('\x83', '')  # FIXME should be stripped beforehand during format norm?
             .lower()  # sigh
                )
    if ident[0].isdigit():
        ident = 'n_' + ident

    return ident


class OrcidPrefixes(oq.OntCuries):
    # set these manually since, sigh, factory patterns
    _dict = {}
    _n_to_p = {}
    _strie = {}
    _trie = {}


OrcidPrefixes({'orcid':'https://orcid.org/',
               'ORCID':'https://orcid.org/',})


class OrcidId(OntId):
    _namespaces = OrcidPrefixes
    __firsts = 'iri',

    class MalformedOrcidError(Exception):
        """ WHAT HAVE YOU DONE!? """

    @property
    def checksumValid(self):
        """ see
        https://support.orcid.org/hc/en-us/articles/360006897674-Structure-of-the-ORCID-Identifier
        """

        try:
            *digits, check_string = self.suffix.replace('-', '')
            check = 10 if check_string == 'X' else int(check_string)
            total = 0
            for digit_string in digits:
                total = (total + int(digit_string)) * 2

            remainder = total % 11
            result = (12 - remainder) % 11
            return result == check
        except ValueError as e:
            raise self.MalformedOrcidError(self) from e


def JT(blob):
    """ this is not a class but is a function hacked to work like one """
    def _populate(blob, top=False):
        if isinstance(blob, list) or isinstance(blob, tuple):
            # TODO alternatively if the schema is uniform, could use bc here ...
            def _all(self, l=blob):  # FIXME don't autocomplete?
                keys = set(k for b in l
                           if isinstance(b, dict)
                           for k in b)
                obj = {k:[] for k in keys}
                _list = []
                _other = []
                for b in l:
                    if isinstance(b, dict):
                        for k in keys:
                            if k in b:
                                obj[k].append(b[k])
                            else:
                                obj[k].append(None)

                    elif any(isinstance(b, t) for t in (list, tuple)):
                        _list.append(JT(b))

                    else:
                        _other.append(b)
                        for k in keys:
                            obj[k].append(None)  # super inefficient

                if _list:
                    obj['_list'] = JT(_list)

                if obj:
                    j = JT(obj)
                else:
                    j = JT(blob)

                if _other:
                    #obj['_'] = _other  # infinite, though lazy
                    setattr(j, '_', _other)

                setattr(j, '_b', blob)
                #lb = len(blob)
                #setattr(j, '__len__', lambda: lb)  # FIXME len()
                return j

            def it(self, l=blob):
                for b in l:
                    if any(isinstance(b, t) for t in (dict, list, tuple)):
                        yield JT(b)
                    else:
                        yield b

            if top:
                # FIXME iter is non homogenous
                return [('__iter__', it), ('_all', property(_all))]
            #elif not [e for e in b if isinstance(self, dict)]:
                #return property(id)
            else:
                # FIXME this can render as {} if there are no keys
                return property(_all)
                #obj = {'_all': property(_all),
                       #'_l': property(it),}

                #j = JT(obj)
                #return j

                #nl = JT(obj)
                #nl._list = blob
                #return property(it)

        elif isinstance(blob, dict):
            if top:
                out = [('_keys', tuple(blob))]
                for k, v in blob.items():  # FIXME normalize keys ...
                    nv = _populate(v)
                    out.append((k, nv))
                    #setattr(cls, k, nv)
                return out
            else:
                return JT(blob)

        else:
            if top:
                raise exc.UnhandledTypeError('asdf')
            else:
                @property
                def prop(self, v=blob):
                    return v

                return prop

    def _repr(self, b=blob):  # because why not
        return 'JT(\n' + repr(b) + '\n)'

    def query(self, *path):
        """ returns None at first failure """
        j = self
        for key in path:
            j = getattr(j, key, None)
            if j is None:
                return

        return j

    #cd = {k:v for k, v in _populate(blob, True)}

    # populate the top level
    cd = {k:v for k, v in ((a, b) for t in _populate(blob, True)
                           for a, b in (t if isinstance(t, list) else (t,)))}
    cd['__repr__'] = _repr
    cd['query'] = query
    nc = type('JT' + str(type(blob)), (object,), cd)
    return nc()


class FileSize(int):
    @property
    def mb(self):
        return self / 1024 ** 2

    @property
    def hr(self):
        """ human readable file size """

        def sizeof_fmt(num, suffix=''):
            for unit in ['','K','M','G','T','P','E','Z']:
                if abs(num) < 1024.0:
                    return "%0.0f%s%s" % (num, unit, suffix)
                num /= 1024.0
            return "%.1f%s%s" % (num, 'Yi', suffix)

        if self is not None and self >= 0:
            return sizeof_fmt(self)
        else:
            return '??'  # sigh

