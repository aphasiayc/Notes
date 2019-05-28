#!/usr/bin/env python
# -*- coding: utf-8 -*- #
from __future__ import unicode_literals

AUTHOR = 'aphasiayc'
SITENAME = 'Notes'
SITEURL = ''

TIMEZONE = 'Asia/Shanghai'

DEFAULT_LANG = 'en'

# Feed generation is usually not desired when developing
FEED_ALL_ATOM = None
CATEGORY_FEED_ATOM = None
TRANSLATION_FEED_ATOM = None
AUTHOR_FEED_ATOM = None
AUTHOR_FEED_RSS = None

# File paths
PATH = 'content'
STATIC_PATHS = ['os']
ARTICLE_PATHS = ['os']
ARTICLE_SAVE_AS = '{date:%Y}/{slug}.html'
ARTICLE_URL = '{date:%Y}/{slug}.html'

# Blogroll
LINKS = (('Pelican', 'http://getpelican.com/'), )

# Social widget
SOCIAL = (('github', 'https://github.com/aphasiayc'),)

DEFAULT_PAGINATION = 10
THEME = 'pelican-clean-blog'

# Uncomment following line if you want document-relative URLs when developing
# RELATIVE_URLS = True
