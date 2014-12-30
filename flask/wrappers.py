# -*- coding: utf-8 -*-
from werkzeug.wrappers import Request as RequestBase, Response as ResponseBase
from werkzeug.exceptions import BadRequest

from .debughelpers import attach_enctype_error_multidict
from . import json
from .globals import _request_ctx_stack


_missing = object()


def _get_data(req, cache):
    getter = getattr(req, 'get_data', None)
    if getter is not None:
        return getter(cache=cache)
    return req.data


class Request(RequestBase):
    """The request object used by default in Flask.  Remembers the
    matched endpoint and view arguments.

    It is what ends up as :class:`~flask.request`.  If you want to replace
    the request object used you can subclass this and set
    :attr:`~flask.Flask.request_class` to your subclass.

    The request object is a :class:`~werkzeug.wrappers.Request` subclass and
    provides all of the attributes Werkzeug defines plus a few Flask
    specific ones.
    """

    #: the internal URL rule that matched the request.  This can be
    #: useful to inspect which methods are allowed for the URL from
    #: a before/after handler (``request.url_rule.methods``) etc.
    #:
    #: .. versionadded:: 0.6
    url_rule = None

    #: a dict of view arguments that matched the request.  If an exception
    #: happened when matching, this will be `None`.
    view_args = None

    #: if matching the URL failed, this is the exception that will be
    #: raised / was raised as part of the request handling.  This is
    #: usually a :exc:`~werkzeug.exceptions.NotFound` exception or
    #: something similar.
    routing_exception = None

    # switched by the request context until 1.0 to opt in deprecated
    # module functionality
    _is_old_module = False

    @property
    def max_content_length(self):
        """Read-only view of the `MAX_CONTENT_LENGTH` config key."""
        ctx = _request_ctx_stack.top
        if ctx is not None:
            return ctx.app.config['MAX_CONTENT_LENGTH']

    @property
    def endpoint(self):
        """The endpoint that matched the request.  This in combination with
        :attr:`view_args` can be used to reconstruct the same or a
        modified URL.  If an exception happened when matching, this will
        be `None`.
        """
        if self.url_rule is not None:
            return self.url_rule.endpoint

    def get_json(self, force=False, silent=False, cache=True):
        """Parses the incoming JSON request data and returns it.  If
        parsing fails the :meth:`on_json_loading_failed` method on the
        request object will be invoked.  By default this function will
        only load the json data if the mimetype is ``application/json``
        but this can be overriden by the `force` parameter.

        :param force: if set to `True` the mimetype is ignored.
        :param silent: if set to `False` this method will fail silently
                       and return `False`.
        :param cache: if set to `True` the parsed JSON data is remembered
                      on the request.
        """
        rv = getattr(self, '_cached_json', _missing)
        if rv is not _missing:
            return rv

        if self.mimetype != 'application/json' and not force:
            return None

        # We accept a request charset against the specification as
        # certain clients have been using this in the past.  This
        # fits our general approach of being nice in what we accept
        # and strict in what we send out.
        request_charset = self.mimetype_params.get('charset')
        try:
            data = _get_data(self, cache)
            if request_charset is not None:
                rv = json.loads(data, encoding=request_charset)
            else:
                rv = json.loads(data)
        except ValueError as e:
            if silent:
                rv = None
            else:
                rv = self.on_json_loading_failed(e)
        if cache:
            self._cached_json = rv
        return rv

    def on_json_loading_failed(self, e):
        """Called if decoding of the JSON data failed.  The return value of
        this method is used by :meth:`get_json` when an error occurred.  The
        default implementation just raises a :class:`BadRequest` exception.

        .. versionchanged:: 0.10
           Removed buggy previous behavior of generating a random JSON
           response.  If you want that behavior back you can trivially
           add it by subclassing.

        .. versionadded:: 0.8
        """
        raise BadRequest()

    def _load_form_data(self):
        RequestBase._load_form_data(self)

        # in debug mode we're replacing the files multidict with an ad-hoc
        # subclass that raises a different error for key errors.
        ctx = _request_ctx_stack.top
        if ctx is not None and ctx.app.debug and \
           self.mimetype != 'multipart/form-data' and not self.files:
            attach_enctype_error_multidict(self)


class BaseResponse(object):
    #: Should this response object correct the location header to be RFC
    #: conformant?  This is true by default.
    #:
    #: .. versionadded:: 0.8
    autocorrect_location_header = True

    def get_wsgi_headers(self, environ):
        """This is automatically called right before the response is started
        and returns headers modified for the given environment.  It returns a
        copy of the headers from the response with some modifications applied
        if necessary.

        For example the location header (if present) is joined with the root
        URL of the environment.  Also the content length is automatically set
        to zero here for certain status codes.

        .. versionchanged:: 0.6
           Previously that function was called `fix_headers` and modified
           the response object in place.  Also since 0.6, IRIs in location
           and content-location headers are handled properly.

           Also starting with 0.6, Werkzeug will attempt to set the content
           length if it is able to figure it out on its own.  This is the
           case if all the strings in the response iterable are already
           encoded and the iterable is buffered.

        :param environ: the WSGI environment of the request.
        :return: returns a new :class:`~werkzeug.datastructures.Headers`
                 object.
        """
        headers = Headers(self.headers)
        location = None
        content_location = None
        content_length = None
        status = self.status_code

        # iterate over the headers to find all values in one go.  Because
        # get_wsgi_headers is used each response that gives us a tiny
        # speedup.
        for key, value in headers:
            ikey = key.lower()
            if ikey == u'location':
                location = value
            elif ikey == u'content-location':
                content_location = value
            elif ikey == u'content-length':
                content_length = value

        # make sure the location header is an absolute URL
        if location is not None:
            old_location = location
            if isinstance(location, text_type):
                # Safe conversion is necessary here as we might redirect
                # to a broken URI scheme (for instance itms-services).
                location = iri_to_uri(location, safe_conversion=True)

            if self.autocorrect_location_header:
                current_url = get_current_url(environ, root_only=True)
                if isinstance(current_url, text_type):
                    current_url = iri_to_uri(current_url)
                location = url_join(current_url, location)
            if location != old_location:
                headers['Location'] = location

        # make sure the content location is a URL
        if content_location is not None and \
           isinstance(content_location, text_type):
            headers['Content-Location'] = iri_to_uri(content_location)

        # remove entity headers and set content length to zero if needed.
        # Also update content_length accordingly so that the automatic
        # content length detection does not trigger in the following
        # code.
        if 100 <= status < 200 or status == 204:
            headers['Content-Length'] = content_length = u'0'
        elif status == 304:
            remove_entity_headers(headers)

        # if we can determine the content length automatically, we
        # should try to do that.  But only if this does not involve
        # flattening the iterator or encoding of unicode strings in
        # the response.  We however should not do that if we have a 304
        # response.
        if self.automatically_set_content_length and \
           self.is_sequence and content_length is None and status != 304:
            try:
                content_length = sum(len(to_bytes(x, 'ascii'))
                                     for x in self.response)
            except UnicodeError:
                # aha, something non-bytestringy in there, too bad, we
                # can't safely figure out the length of the response.
                pass
            else:
                headers['Content-Length'] = str(content_length)

        return headers
