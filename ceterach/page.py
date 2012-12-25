#!/usr/bin/python3
# -*- coding: utf-8 -*-
#-------------------------------------------------------------------------------
# This file is part of Ceterach.
# Copyright (C) 2012 Andrew Wang <andrewwang43@gmail.com>
# Copyright (C) 2012 Kunal Mehta <legoktm@gmail.com>
#
# Ceterach is free software; you can redistribute it and/or modify it under
# the terms of the GNU Lesser General Public License as published by the Free
# Software Foundation; either version 2.1 of the License, or (at your option)
# any later version.
#
# Ceterach is distributed in the hope that it will be useful, but WITHOUT ANY
# WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
# FOR A PARTICULAR PURPOSE.  See the GNU Lesser General Public License for more
# details.
#
# You should have received a copy of the GNU Lesser General Public License
# along with Ceterach.  If not, see <http://www.gnu.org/licenses/>.
#-------------------------------------------------------------------------------

import re
import hashlib
from datetime import datetime
from time import strftime, gmtime

from . import exceptions as exc

__all__ = ["Page"]

def blah(obj, attr):
    if not hasattr(obj, attr):
        obj.load_attributes()
    return getattr(obj, attr)

def blah_with_exc(obj, attr):
    if obj.exists:
        return getattr(obj, attr)
    else:
        err = "Page  {0!r} does not exist".format(obj.title)
        raise exc.NonexistentPageError(err)

class Page:
    """
    This represents a page on a wiki, and has attributes that ease the process
    of getting information about the page.
    """
    def __init__(self, api, title='', pageid=0, follow_redirects=False):
        self._api = api
        if pageid is 0 and title is '':
            err = "You must specify either the 'title' or 'pageid' parameters"
            raise TypeError(err)
        elif pageid is not 0 and title is not '':
            err = "You cannot specify both the 'title' and 'pageid' parameters"
            raise TypeError(err)
        self._title = title
        self._pageid = pageid
        self.follow_redirects = follow_redirects

    def load_attributes(self, res=None):
        """
        Call this to load ``self.__title``, ``._is_redirect``, ``._pageid``,
        ``._exists``, ``._namespace``, ``._creator``, and ``._revid``.

        This method also resolves redirects if ``follow_redirects=True`` was
        passed to the constructor. If the page is a redirect, the method will
        make a grand total of 2 API queries.

        If the *res* parameter was supplied, the method will pretend that
        was what the first query returned. As such, if redirects are followed,
        a single API query will be made.

        :type res: dict
        :param res: The result of an earlier API request (optional).

                    If you are planning
                    to set this parameter, the minimal API request parameters
                    needed for this method to function correctly are:

                    ``{'inprop': ('protection',),
                    'prop': ('info', 'revisions', 'categories'),
                    'rvprop': ('user', 'content')}``
        """
        self.__load(res)
        if self.follow_redirects and self.is_redirect:
            self._title = self.redirect_target.title
            del self._content
            self.__load(None)

    def __load(self, res):
        i = self._api.iterator
        prop = ('info', 'revisions', 'categories')
        inprop = ("protection",)
        rvprop = ('user', 'content')
        kwargs = {"prop": prop, "rvprop": rvprop, "inprop": inprop,
                  "rvlimit": 1, "rvdir": "older"
        }
        if self.title != '':
            kwargs['titles'] = self.title
        elif self.pageid != 0:
            kwargs['pageids'] = self.pageid
        else:
            raise exc.CeterachError("WTF")
        res = res or list(i(1, **kwargs))[0]
        # Normalise the page title in case it was entered oddly
        self._title = res['title']
        self._is_redirect = 'redirect' in res
        self._pageid = res.get("pageid", -1)
        if self._pageid < 0:
            if "missing" in res:
                # If it has a negative ID and it's missing, we can still get
                # the namespace...
                self._exists = False
            else:
                # ... but not if it's also invalid
                self._exists = False
                err = "Page {0!r} is invalid"
                raise exc.InvalidPageError(err.format(self.title))
        else:
            self._exists = True
            self._content = res['revisions'][0]["*"]
        self._namespace = res["ns"]
        self._is_talkpage = self._namespace % 2 == 1 # talkpages have odd IDs
        self._protection = {"edit": (None, None),
                            "move": (None, None),
                            "create": (None, None),
        }
        if res.get("protection", None):
            for info in res['protection']:
                expiry = info['expiry']
                if expiry == 'infinity':
                    expiry = datetime.max
                else:
                    expiry = datetime.strptime(expiry, "%Y-%m-%dT%H:%M:%SZ")
                self._protection[info['type']] = info['level'], expiry
        # These last two fields will only be specified if the page exists:
        try:
            self._revision_user = self._api.user(res['revisions'][0]['user'])
            self._revid = res['lastrevid']
        except KeyError:
            self._revision_user = None
            self._revid = None
        c = self._api.category
        cats = res.get("categories", "")
        self._categories = tuple(c(x['title']) for x in cats)

    def __edit(self, content, summary, minor, bot, force, edittype):
        title = self.title
        try:
            token = self._api.tokens['edit']
        except KeyError:
            self._api.set_token("edit")
            if not 'edit' in self._api.tokens:
                err = "You do not have the edit permission"
                raise exc.PermissionError(err)
            token = self._api.tokens['edit']
        edit_params = {"action": "edit", "title": title, "text": content,
                       "token": token, "summary": summary}
        edit_params['notbot'] = 1
        edit_params['notminor'] = 1
        edit_params['nocreate'] = 1
        if minor:
            edit_params['minor'] = edit_params.pop("notminor")
        if bot:
            edit_params['bot'] = edit_params.pop("notbot")
        if title.lower().startswith("special:"):
            err = "Pages in the Special namespace can't be edited"
            raise exc.InvalidPageError(err)
        if force is False:
            detect_ec = {"prop": "revisions", "rvprop": "timestamp", "titles": title}
            ec_timestamp_res = tuple(self._api.iterator(1, **detect_ec))[0]
            if 'missing' in ec_timestamp_res and edittype != 'create':
                err = "Use the 'create' method to create pages"
                raise exc.NonexistentPageError(err)
            elif ec_timestamp_res['ns'] == -1:
                err = "Invalid page titles can't be edited"
                raise exc.InvalidPageError(err)
            if edittype != 'create':
                ec_timestamp = ec_timestamp_res['revisions'][0]['timestamp']
                edit_params['basetimestamp'] = ec_timestamp
                edit_params['starttimestamp'] = strftime("%Y-%m-%dT%H:%M:%SZ", gmtime())
            # Add a checksum to ensure that the text is not corrupted
            edit_params['md5'] = hashlib.md5(content.encode("utf-8")).hexdigest()
        if edittype == 'append':
            edit_params['appendtext'] = edit_params.pop("text")
        elif edittype == 'prepend':
            edit_params['prependtext'] = edit_params.pop("text")
        elif edittype == 'create':
            edit_params['createonly'] = edit_params.pop("nocreate")
        res = self._api.call(**edit_params)
        if res['edit']['result'] == "Success":
            # Some attributes are now out of date
            del self._content
            self._exists = True
            self._revid = res['edit']['newrevid']
            self._title = res['edit']['title'] # Normalise the title again
        return res

    def edit(self, content, summary="", minor=False, bot=False, force=False):
        """
        Replaces the page's content with *content*. *summary* is the edit
        summary used for the edit. The edit will be marked as minor if *minor*
        is True, and if *bot* is True and the logged-in user has the bot flag,
        it will also be marked as a bot edit.

        Set *force* to True in order to make the edit even if there's an edit
        conflict or the page was deleted/recreated while the method executed.

        :type content: str
        :param content: The text with which to replace the page's original
                        content.
        :type summary: str
        :param summary: The comment to use for the modification, sometimes
                        known as the edit summary.
        :type minor: bool
        :param minor: Mark the edit as minor, if set to True.
        :type bot: bool
        :param bot: Mark the edit as a bot edit, if the logged in user has the
                    bot flag and the parameter is set to True.
        :type force: bool
        :param force: If set to True, ignore edit conflicts and create the
                      page if it doesn't already exist.
        :returns: A dictionary containing the API query result
        """
        return self.__edit(content, summary, minor, bot, force, 'standard')

    def create(self, content, summary="", minor=False, bot=False, force=False):
        return self.__edit(content, summary, minor, bot, force, 'create')

    def append(self, content, summary="", minor=False, bot=False, force=False):
        return self.__edit(content, summary, minor, bot, force, 'append')

    def prepend(self, content, summary="", minor=False, bot=False, force=False):
        return self.__edit(content, summary, minor, bot, force, 'prepend')

    def move(self, target, reason, *args, **kwargs):
        move_params = {"action": "move", "from": self.title,
                       "to": target, "reason": reason
        }
        try:
            move_params['token'] = self._api.tokens['move']
        except KeyError:
            self._api.set_token("move")
            if not 'move' in self._api.tokens:
                err = "You do not have the move permission"
                raise exc.PermissionError(err)
        allowed = ("movetalk", "movesubpages", "noredirect", "watch", "unwatch")
        for arg in args + tuple(kwargs):
            if arg in allowed:
                move_params[arg] = 1
        return self._api.call(**move_params)

    def from_revid(self, revid):
        """
        Returns a Page object for the given revid.

        This method does not follow redirects, and the very process of calling
        the method makes an API query.
        """
        kwargs = {"prop": ("info", "revisions", "categories"),
                  "inprop": "protection",
                  "rvprop": ("user", "content"),
                  "revids": revid,
        }
        res = self._api.iterator(**kwargs)
        p = self._api.page("some random title")
        p.load_attributes(tuple(res)[0])
        return p

    @property
    def title(self):
        """
        Returns the page's title. If self.load_attributes() was not called
        prior to the execution of this method, the result will be equal to the
        *title* parameter passed to the constructor. Otherwise, it will be
        normalised.
        """
        return self._title

    @property
    def pageid(self):
        """
        An integer ID representing the page.
        """
        return self._pageid

    @property
    def content(self):
        """
        Returns the page content, which is cached if you try to get this
        attribute again.

        If the page does not exist, the method raises a NonexistentPageError.

        :returns: The page content
        :raises: NonexistentPageError
        """
        try:
            return blah(self, "_content")
        except AttributeError:
            err = True
        if err:
            return blah_with_exc(self, "_content")

    @property
    def exists(self):
        """
        Check the existence of the page.

        :returns: True if the page exists, False otherwise
        """
        return blah(self, "_exists")

    @property
    def is_talkpage(self):
        """
        Check if this page is in a talk namespace.

        :returns: True if the page is in a talk namespace, False otherwise
        """
        return blah(self, "_is_talkpage")

    @property
    def revision_user(self):
        """
        Returns the username or IP of the last user to edit the page.

        :returns: A User object
        :raises: NonexistentPageError, if the page doesn't exist or is invalid.
        """
        try:
            return blah(self, "_revision_user")
        except AttributeError:
            err = True
        if err:
            return blah_with_exc(self, "_revision_user")

    @property
    def redirect_target(self):
        """
        Gets the Page object for the target this Page redirects to.

        If this Page doesn't exist, or is invalid, it will
        raise a NonexistentPageError, or InvalidPageError respectively.

        :returns: Page object that represents the redirect target.
        :raises: NonexistentPageError, InvalidPageError
        """
        if not self.exists:
            raise exc.NonexistentPageError("Page does not exist")
        if not self.is_redirect:
            self._redirect_target = None
            return None
        redirect_regex = re.compile(r"#redirect\s*?\[\[(.+?)\]\]", re.I)
        try:
            target = redirect_regex.match(self.content).group(1)
            self._redirect_target = self._api.page(target)
        except AttributeError:
            self._redirect_target = None
        return self._redirect_target

    @property
    def is_redirect(self):
        """
        :returns: True if the page is a redirect, False if the Page isn't or
                  doesn't exist.
        """
        return blah(self, "_is_redirect")

    @property
    def namespace(self):
        """
        :returns: An integer representing the Page's namespace.
        """
        return blah(self, "_namespace")

    @property
    def protection(self):
        """
        Get the protection levels on the page.

        :returns: A dict representing the page's protection level. The keys
                  are, by default, 'edit', 'create', and 'move'. If the wiki
                  is configured to have other protection types, those types
                  will also be included in the keys. The values can be
                  (None, None) (no restriction for that action) or (level,
                  expiry):

                  - level is the userright needed to perform the action
                    ('autoconfirmed', for example)
                  - expiry is the expiration time of the restriction. This will
                    either be None, or a datetime at which the protection will
                    expire.
        """
        return blah(self, "_protection")

    @property
    def revid(self):
        """
        :returns: An integer representing the Page's current revision ID.
        """
        try:
            return blah(self, "_revid")
        except AttributeError:
            err = True
        if err:
            return blah_with_exc(self, "_revid")

    @property
    def categories(self):
        return blah(self, "_categories")
