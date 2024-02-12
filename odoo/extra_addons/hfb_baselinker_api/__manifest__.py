# -*- coding: utf-8 -*-
##############################################################################
#
#   Odoo, Open ERP Source Management Solution
#   Copyright (C) 2022 Hadron for business sp. z o.o. (http://www.hadron.eu.com)
#
#   This program is free software: you can redistribute it and/or modify
#   it under the terms of the GNU Affero General Public License as
#   published by the Free Software Foundation, either version 3 of the
#   License, or (at your option) any later version.
#
#   This program is distributed in the hope that it will be useful,
#   but WITHOUT ANY WARRANTY; without even the implied warranty of
#   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#   GNU Affero General Public License for more details.
#
#   You should have received a copy of the GNU Affero General Public License
#   along with this program.  If not, see <http://www.gnu.org/licenses/>.
#
##############################################################################
{
	'name': "Baselinker API Integration",
	'summary': """baselinker""",
	'description': """Baselinker API Interface Inegration for Odoo15""",
	'author': "Hadron for business sp z.o.o.",
	'website': "http://www.hadronforbusness.com",
	'category': 'tools',
	'version': '2.0.1',
	'license': 'Other proprietary',
	'depends': ['base', 'auth_signup', 'mail','contacts','product','purchase','sale', 'web','stock','sale_management','account','account_accountant','website_sale'],
	'data': [
		'security/ir.model.access.csv',
		'views/extend.xml',
		'views/cron.xml',
	],
	'css': ['static/src/css/hfb_baselinker.css'],
	'installable': True,
	'application': True,
	#'assets': {
	#	'web.assets_backend': [
	#		'hfb_baselinker_api/static/src/js/website_sale_video_field_preview.js',
	#		'hfb_baselinker_api/static/src/js/website_sale_backend.js',
	#		'hfb_baselinker_api/static/src/scss/website_sale_dashboard.scss',
	#		'hfb_baselinker_api/static/src/scss/website_sale_backend.scss',
	#	],
	#}
}
# -*- coding: utf-8 -*-
