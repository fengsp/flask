# -*- coding: utf-8 -*-
from werkzeug.wrappers import Request as RequestBase, Response as ResponseBase
from werkzeug.exceptions import BadRequest

from .debughelpers import attach_enctype_error_multidict
from . import json
from .globals import _request_ctx_stack


_missing = object()


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
