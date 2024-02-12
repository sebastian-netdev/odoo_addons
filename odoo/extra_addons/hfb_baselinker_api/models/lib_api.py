# -*- coding: utf-8 -*-
##############################################################################
#
#	Odoo, Open ERP Source Management Solution
#	Copyright (C) 2022 Hadron for business sp. z o.o. (http://www.hadron.eu.com)
#
#	This program is free software: you can redistribute it and/or modify
#	it under the terms of the GNU Affero General Public License as
#	published by the Free Software Foundation, either version 3 of the
#	License, or (at your option) any later version.
#
#	This program is distributed in the hope that it will be useful,
#	but WITHOUT ANY WARRANTY; without even the implied warranty of
#	MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#	GNU Affero General Public License for more details.
#
#	You should have received a copy of the GNU Affero General Public License
#	along with this program.  If not, see <http://www.gnu.org/licenses/>.
#
##############################################################################
""" @version	2.0.1
	@owner  Hadron for Business
	@author andrzej wiśniewski warp3r
	@date   2022.11.02
	
"""
import os
import os.path
import json
import base64
import uuid
import random
import inspect
import datetime
import time
import re
import ast
from time import mktime
from datetime import datetime
from datetime import timedelta
import requests
from odoo import api, http, fields, models, tools, SUPERUSER_ID, exceptions, _
from odoo.modules import get_module_resource
from odoo.exceptions import AccessError, UserError, RedirectWarning, ValidationError, Warning
from odoo.http import request

import logging
_logger = logging.getLogger(__name__)

API_METHOD = ['get','post','delete','put','patch']

HFB_DEBUG = False

import itertools
from collections import defaultdict
from odoo.osv import expression

from odoo.addons.website.tools import get_video_embed_code


""" 
	Extend Base Models

"""
class ResUsers(models.Model):
	_inherit = 'res.users'

	def _notify_inviter(self):		
		for user in self:
			invite_partner = user.create_uid.partner_id
			_logger.info("""
	ResUsers._notify_inviter()
			if invite_partner:
				# notify invite user that new user is connected
				self.env['bus.bus']._sendone(invite_partner, 'res.users/connection', {
					'username': user.name,
					'partnerId': user.partner_id.id,
				})
			""")

	def action_reset_password(self):
		""" create signup token for each user, and send their signup url by email """
		if self.env.context.get('install_mode', False):
			return
		if self.filtered(lambda user: not user.active):
			raise UserError(_("You cannot perform this action on an archived user."))
		# prepare reset password signup
		create_mode = bool(self.env.context.get('create_user'))

		# no time limit for initial invitation, only for reset password
		expiration = False if create_mode else now(days=+1)

		self.mapped('partner_id').signup_prepare(signup_type="reset", expiration=expiration)

		# send email to users with their signup url
		template = False
		if create_mode:
			try:
				template = self.env.ref('auth_signup.set_password_email', raise_if_not_found=False)
			except ValueError:
				pass
		if not template:
			template = self.env.ref('auth_signup.reset_password_email')
		assert template._name == 'mail.template'

		email_values = {
			'email_cc': False,
			'auto_delete': True,
			'recipient_ids': [],
			'partner_ids': [],
			'scheduled_date': False,
		}
		
		for user in self:
			if user.notification_type == 'email':
				if not user.email:
					raise UserError(_("Cannot send email: user %s has no email address.", user.name))
				email_values['email_to'] = user.email
				# TDE FIXME: make this template technical (qweb)
				with self.env.cr.savepoint():
					force_send = not(self.env.context.get('import_file', False))
					template.send_mail(user.id, force_send=force_send, raise_exception=True, email_values=email_values)
				_logger.info("Password reset email sent for user <%s> to <%s>", user.login, user.email)


class Picking(models.Model):
	_inherit = 'stock.picking'

	@api.depends('move_type', 'immediate_transfer', 'move_lines.state', 'move_lines.picking_id')
	def _compute_state(self):
		''' State of a picking depends on the state of its related stock.move
		- Draft: only used for "planned pickings"
		- Waiting: if the picking is not ready to be sent so if
		  - (a) no quantity could be reserved at all or if
		  - (b) some quantities could be reserved and the shipping policy is "deliver all at once"
		- Waiting another move: if the picking is waiting for another move
		- Ready: if the picking is ready to be sent so if:
		  - (a) all quantities are reserved or if
		  - (b) some quantities could be reserved and the shipping policy is "as soon as possible"
		- Done: if the picking is done.
		- Cancelled: if the picking is cancelled
		'''
		picking_moves_state_map = defaultdict(dict)
		picking_move_lines = defaultdict(set)
		for move in self.env['stock.move'].search([('picking_id', 'in', self.ids)]):
			picking_id = move.picking_id
			move_state = move.state
			picking_moves_state_map[picking_id.id].update({
				'any_draft': picking_moves_state_map[picking_id.id].get('any_draft', False) or move_state == 'draft',
				'all_cancel': picking_moves_state_map[picking_id.id].get('all_cancel', True) and move_state == 'cancel',
				'all_cancel_done': picking_moves_state_map[picking_id.id].get('all_cancel_done', True) and move_state in ('cancel', 'done'),
				'all_done_are_scrapped': picking_moves_state_map[picking_id.id].get('all_done_are_scrapped', True) and (move.scrapped if move_state == 'done' else True),
				'any_cancel_and_not_scrapped': picking_moves_state_map[picking_id.id].get('any_cancel_and_not_scrapped', False) or (move_state == 'cancel' and not move.scrapped),
			})
			picking_move_lines[picking_id.id].add(move.id)
		for picking in self:
			picking_id = (picking.ids and picking.ids[0]) or picking.id
			state = picking.state
			if not picking_moves_state_map[picking_id]:
				picking.state = 'draft'
			elif picking_moves_state_map[picking_id]['any_draft']:
				picking.state = 'draft'
			elif picking_moves_state_map[picking_id]['all_cancel']:
				picking.state = 'cancel'
			elif picking_moves_state_map[picking_id]['all_cancel_done']:
				if picking_moves_state_map[picking_id]['all_done_are_scrapped'] and picking_moves_state_map[picking_id]['any_cancel_and_not_scrapped']:
					picking.state = 'cancel'
				else:
					picking.state = 'done'
			else:
				relevant_move_state = self.env['stock.move'].browse(picking_move_lines[picking_id])._get_relevant_state_among_moves()
				if picking.immediate_transfer and relevant_move_state not in ('draft', 'cancel', 'done'):
					picking.state = 'assigned'
				elif relevant_move_state == 'partially_available':
					picking.state = 'assigned'
				else:
					picking.state = relevant_move_state

			#state = self.state
			#super(Picking,self)._compute_state()
			if picking.origin and state in ['waiting','confirmed'] and picking.state in ['assigned','done']:
				orders = self.env['sale.order'].search([('name','=', picking.origin)])
				for order in orders:
					if (order.bl_order_id and int(order.bl_order_id) != 0):
						order.bl_status = 'ready'
						order.set_order_status()
		


class hfbSystem(models.Model):
	_name = 'hfbsystem'

	name = fields.Char(string='Name', store=True, )

	def init(self, force=False):
		if not self.env['ir.config_parameter'].sudo().get_param('bl_order_sync_state'):
			self.env['ir.config_parameter'].sudo().set_param('bl_order_sync_state', 'idle')
		value = self.env['ir.config_parameter'].sudo().get_param('bl_order_sync_state')
		msg = """\n\tINIT::ir.config_parameter.bl_order_sync_state => %s
		__________________________________________________________________""" % (value)
		if self.env['ir.config_parameter'].sudo().get_param('baselinker_set_debug'): _logger.info(msg)

	def get_config(self):
		s = ""
		for val in self.env:
			s += "\n%s = %s" % (val, self.env.get(val))
		if self.env['ir.config_parameter'].sudo().get_param('baselinker_set_debug'): _logger.info("""\n\t_defaults:: %s""" % s)
		

	def info(self):
		config = self.env['res.config.settings'].sudo().get_values()

		limit_time_cpu = config.get('limit_time_cpu')
		limit_memory_soft = config.get('limit_memory_soft')
		limit_memory_soft = config.get('limit_memory_soft')
		limit_time_real = config.get('limit_time_real')
		limit_time_real_cron = config.get('limit_time_real_cron')

		msg = """
	Base Config Parameters:

	limit_time_cpu_______%s
	limit_memory_soft____%s
	limit_memory_soft____%s
	limit_time_real______%s
	limit_time_real_cron_%s

	config %s
_________________________________________________________""" % ( limit_time_cpu, limit_memory_soft, limit_memory_soft, limit_time_real, limit_time_real_cron, config)

		if self.env['ir.config_parameter'].sudo().get_param('baselinker_set_debug'): _logger.info(msg)



class Location(models.Model):
	_inherit = "stock.location"

	name = fields.Char(translate=False)

	bl_inventorywarehouses_id = fields.Many2one('bl_inventorywarehouses', string="Magazyn BaseLinker", )
	#bl_inventorywarehouses_ids = fields.Many2many('bl_inventorywarehouses', string="Magazyny BaseLinker", )



class StockMove(models.Model):
	_inherit = 'stock.move'

	bl_order_product_id = fields.Char('Baselinker Order Product ID')
	
"""	=======================================================================================
	Settings Model Class REST API
"""
class APIConfigSettings(models.Model):
	_name = 'baselinker.config.settings'
	_description = 'baselinker.config.settings'

	""" wymagane """
	baselinker_api_login = fields.Char(string="User Login", default="login", )
	baselinker_api_passwd = fields.Char(string="Password", default="paswword", )
	baselinker_api_token_key = fields.Char(string="App Key", default="token-API", )
	baselinker_api_url = fields.Char(string="URL", default="https://api.baselinker.com/connector.php", )
	baselinker_inventory_id = fields.Char("Default Catalog_ID", default='0', )
	baselinker_price_group_id = fields.Integer('Default PriceGroup_ID', default=0, )
	baselinker_warehouse_id = fields.Integer('Default Warehouse_ID', default=0, )
	baselinker_products_restore = fields.Boolean('Restore product if not exist', default=False, )
	baselinker_website_id = fields.Many2one('website', string="Default website" )
	baselinker_client_invitation = fields.Boolean(default=False)
	baselinker_get_orders_by_status_id = fields.Many2one('baselinker.status')
	""" pomocnicze """
	baselinker_product_create_limit = fields.Integer('Product Create Limit')
	baselinker_product_update_limit = fields.Integer('Product Update Limit')
	baselinker_query_update_period = fields.Integer(string="period", default=90, )
	baselinker_api_connection_timeout = fields.Integer(string="timeout", default=5)
	baselinker_api_query_limit = fields.Integer(string="Single query limit", store=True, default=100, )
	baselinker_query_date = fields.Selection( [
		('y','Current Year'),
		('l120','Last 120 days'),
		('l30','Last 30 days'),
		('m','Current Month'),
		('w','Last Week'),
		('d','Current Day'),
		('h','Current Hour')
		], default='d', store=True, string="Query from current")
	baselinker_get_unconfirmed_orders = fields.Boolean('Download unconfirmed orders as well', default=False, store=True, )
	baselinker_get_external_invoices = fields.Boolean('Download external invoices as well', default=False, store=True, )
	baselinker_delivery_product_id = fields.Many2one('product.product',string="Delivery Product")
	baselinker_set_debug = fields.Boolean('DEBUG in Logfile', default=False, store=True, )
	baselinker_skip_bundle = fields.Boolean('Skip create bundle', default=True, store=True, )


class ResConfigSettings(models.TransientModel):
	_inherit = 'res.config.settings'
	#_name = 'res.config.settings'

	""" wymagane """
	baselinker_api_login = fields.Char(string="User Login", default="login", )
	baselinker_api_passwd = fields.Char(string="Password", default="password", )
	baselinker_api_token_key = fields.Char(string="API Token", default="token-API", )
	baselinker_api_url = fields.Char(string="URL", default="https://api.baselinker.com/connector.php", )
	baselinker_inventory_id = fields.Char("Primary Catalog_ID", default='0',  )
	baselinker_price_group_id = fields.Integer('Primary PriceGroup_ID', default=0,  )
	baselinker_warehouse_id = fields.Integer('Default Warehouse_ID', default=0, )
	baselinker_products_restore = fields.Boolean('Restore product if not exist', default=False)
	baselinker_website_id = fields.Many2one('website', string="Default website" )
	baselinker_client_invitation = fields.Boolean(default=False)
	baselinker_get_orders_by_status_id = fields.Many2one('baselinker.status')
	""" pomocnicze """
	baselinker_product_create_limit = fields.Integer('Product Create Limit')
	baselinker_product_update_limit = fields.Integer('Product Update Limit')
	baselinker_query_update_period = fields.Integer(string="period", default=90, )
	baselinker_api_connection_timeout = fields.Integer(string="timeout", default=5)
	baselinker_api_query_limit = fields.Integer(string="Single query limit", store=True, default=100, )
	baselinker_query_date = fields.Selection( [
		('y','Current Year'),
		('l120','Last 120 days'),
		('l30','Last 30 days'),
		('m','Current Month'),
		('w','Last Week'),
		('d','Current Day'),
		('h','Current Hour')
		], default='d', store=True, string="Query from current")
	baselinker_get_unconfirmed_orders = fields.Boolean('Download unconfirmed orders as well', default=False, store=True, )
	baselinker_get_external_invoices = fields.Boolean('Download external invoices as well', default=False, store=True, )
	baselinker_delivery_product_id = fields.Many2one('product.product',string="Delivery Product")
	baselinker_set_debug = fields.Boolean('DEBUG in Logfile', default=False, store=True, )
	baselinker_skip_bundle = fields.Boolean('Skip create bundle', default=True, store=True, )
	""" do usuniecia """
	baselinker_client_id = fields.Integer()

	#@api.model
	def odoo_dbname(self):
		#query = "conninfo"
		#self.env.cr.execute(query)
		#rows = self.env.cr.dictfetchall()
		msg = """
	dbName = %s
__________________________________________""" % ( self.env.cr.dbname )
		raise UserError(msg)


	def set_bl_order_sync_state(self):
		self.env['ir.config_parameter'].sudo().set_param('bl_order_sync_state', 'idle')


	def set_values(self):
		super(ResConfigSettings, self).set_values()
		params = self.env['ir.config_parameter'].sudo()
		values = {
			'baselinker_api_login': self.baselinker_api_login,
			'baselinker_api_passwd': self.baselinker_api_passwd,
			'baselinker_api_token_key': self.baselinker_api_token_key,
			'baselinker_api_url': self.baselinker_api_url,
			'baselinker_inventory_id': self.baselinker_inventory_id or None,
			'baselinker_price_group_id': self.baselinker_price_group_id or None,
			'baselinker_warehouse_id': self.baselinker_warehouse_id or None, 
			'baselinker_products_restore': self.baselinker_products_restore,
			'baselinker_query_update_period': self.baselinker_query_update_period,
			'baselinker_api_connection_timeout': self.baselinker_api_connection_timeout,
			'baselinker_api_query_limit': self.baselinker_api_query_limit,
			'baselinker_query_date': self.baselinker_query_date,
			'baselinker_get_unconfirmed_orders': self.baselinker_get_unconfirmed_orders,
			'baselinker_website_id': self.baselinker_website_id or None,
			'baselinker_client_invitation': self.baselinker_client_invitation or False,
			'baselinker_get_external_invoices': self.baselinker_get_external_invoices,
			'baselinker_get_orders_by_status_id': self.baselinker_get_orders_by_status_id or None,
			'baselinker_delivery_product_id': self.baselinker_delivery_product_id or None,
			'baselinker_product_create_limit': self.baselinker_product_create_limit,
			'baselinker_product_update_limit': self.baselinker_product_update_limit,
			'baselinker_set_debug': self.baselinker_set_debug,
			'baselinker_skip_bundle': self.baselinker_skip_bundle,
		}
		params.set_param('baselinker_api_login', self.baselinker_api_login)
		params.set_param('baselinker_api_passwd', self.baselinker_api_passwd)
		params.set_param('baselinker_api_token_key', self.baselinker_api_token_key)
		params.set_param('baselinker_api_url', self.baselinker_api_url)
		params.set_param('baselinker_inventory_id', self.baselinker_inventory_id or None)
		params.set_param('baselinker_warehouse_id', self.baselinker_warehouse_id or None)
		params.set_param('baselinker_website_id', self.baselinker_website_id or None)
		params.set_param('baselinker_client_invitation', self.baselinker_client_invitation or False)
		params.set_param('baselinker_products_restore', self.baselinker_products_restore)
		params.set_param('baselinker_price_group_id', self.baselinker_price_group_id or None)
		params.set_param('baselinker_query_update_period', self.baselinker_query_update_period)
		params.set_param('baselinker_api_connection_timeout', self.baselinker_api_connection_timeout)
		params.set_param('baselinker_api_query_limit', self.baselinker_api_query_limit)
		params.set_param('baselinker_query_date', self.baselinker_query_date)
		params.set_param('baselinker_get_unconfirmed_orders', self.baselinker_get_unconfirmed_orders)
		params.set_param('baselinker_get_external_invoices', self.baselinker_get_external_invoices)
		params.set_param('baselinker_get_orders_by_status_id', self.baselinker_get_orders_by_status_id or None)
		params.set_param('baselinker_delivery_product_id', self.baselinker_delivery_product_id or None)
		params.set_param('baselinker_product_create_limit', self.baselinker_product_create_limit)
		params.set_param('baselinker_product_update_limit', self.baselinker_product_update_limit)
		params.set_param('baselinker_set_debug', self.baselinker_set_debug)
		params.set_param('baselinker_skip_bundle', self.baselinker_skip_bundle)

		api = self.env['baselinker.config.settings'].sudo().search([('id','=',1)])
		if not api:
			_logger.info("""\n\n%s """ % values ) #json.dumps(values, indent=4, sort_keys=True ))
			api = self.env['baselinker.config.settings'].sudo().create(values)
		else:
			api.sudo().write(values)


	@api.model
	def get_values(self):
		context = dict(self._context or {})
		res = super(ResConfigSettings, self).get_values()
		#params = self.env['ir.config_parameter'].sudo()
		api = self.env['baselinker.config.settings'].search([('id','=',1)])
		res.update(
			baselinker_api_login = api.baselinker_api_login,
			baselinker_api_passwd = api.baselinker_api_passwd,
			baselinker_api_token_key = api.baselinker_api_token_key,
			baselinker_api_url = api.baselinker_api_url,
			baselinker_inventory_id = api.baselinker_inventory_id,
			baselinker_price_group_id = api.baselinker_price_group_id,
			baselinker_warehouse_id = api.baselinker_warehouse_id,
			baselinker_website_id = api.baselinker_website_id,
			baselinker_client_invitation = api.baselinker_client_invitation,
			baselinker_products_restore = api.baselinker_products_restore,
			baselinker_query_update_period = api.baselinker_query_update_period,
			baselinker_api_connection_timeout = api.baselinker_api_connection_timeout,
			baselinker_api_query_limit = api.baselinker_api_query_limit,
			baselinker_query_date = api.baselinker_query_date,
			baselinker_get_unconfirmed_orders = api.baselinker_get_unconfirmed_orders,
			baselinker_get_external_invoices = api.baselinker_get_external_invoices,
			baselinker_get_orders_by_status_id = api.baselinker_get_orders_by_status_id,
			baselinker_delivery_product_id = api.baselinker_delivery_product_id,
			baselinker_product_create_limit = api.baselinker_product_create_limit,
			baselinker_product_update_limit = api.baselinker_product_update_limit,
			baselinker_set_debug = api.baselinker_set_debug,
			baselinker_skip_bundle = api.baselinker_skip_bundle,
		)

		return res

	
	def button_get_default_Warehouse(self):
		bl = self.env['baselinker']
		bl.getInventoryWarehouses()
	
	def button_get_default_PriceGroups(self):
		bl = self.env['baselinker']
		bl.getInventoryPriceGroups()
	
	def button_get_default_Inventories(self):
		bl = self.env['baselinker']
		bl.getInventories()
		#return {
		#	'type': 'ir.actions.client',
		#	'tag': 'reload',
		#}


"""	=======================================================================================
	Primary Class REST API
"""
class BaseLinkerAPI(models.Model):
	_name = 'blapi'
	_description = 'Base Linker API'

	"""	predefiniowane	"""
	token_type	= None
	expires_in	= None

	login		= fields.Char(default="login", )
	passwd		= fields.Char(default="passwd", )
	tokenkey	= fields.Char(default="token-API", )
	clientkey	= fields.Char(default="client-Token", )
	url			= fields.Char(default="https://api.baselinker.com/connector.php", )
	oauthurl	= fields.Char(default="https://api.baselinker.com/connector.php", )

	inventory_id = fields.Char("Primary Catalog_ID", default='0', )
	price_group_id = fields.Integer('Primary PriceGroup_ID', default=0, )
	warehouse_id = fields.Integer('Default Warehouse_ID', default=0, )
	connection_timeout = fields.Integer(string="timeout", default=5)
	baselinker_website_id = fields.Many2one('website', string="Default website" )
	baselinker_client_invitation = fields.Boolean(default=False)
	baselinker_api_query_limit = fields.Integer(string="Single query limit", store=True, default=50, )
	baselinker_query_date = fields.Selection( [
		('y','Current Year'),
		('l120','Last 120 days'),
		('l30','Last 30 days'),
		('m','Current Month'),
		('w','Last Week'),
		('d','Current Day'),
		('h','Current Hour')
		], default='d', store=True, string="Query from current")
	baselinker_get_unconfirmed_orders = fields.Boolean('Download unconfirmed orders as well', default=False, store=True, )
	baselinker_get_external_invoices = fields.Boolean(default=False, store=True)
	baselinker_products_restore = fields.Boolean('Restore product if not exist', default=False, )
	baselinker_get_orders_by_status_id = fields.Many2one('baselinker.status')
	baselinker_delivery_product_id = fields.Many2one('product.product')
	baselinker_product_create_limit = fields.Integer('Product Create Limit')
	baselinker_product_update_limit = fields.Integer('Product Update Limit')
	baselinker_set_debug = fields.Boolean('DEBUG in Logfile', default=False, store=True, )
	baselinker_skip_bundle = fields.Boolean('Skip create bundle', default=True, store=True, )
	error = fields.Boolean(default=False, )
	# static model
	name = fields.Char('Name', default="/", )
	direction = fields.Selection([
		('in','incomming'),
		('out','outgoing')
		], store=True, default='in')
	state = fields.Selection([
		('sent','Sent'),
		('resp','Response'),
		('err','Error'),
		('draft','Prepared'),
		('wait','Waiting')
		], store=True, default='draft')
	surl = fields.Text(default="https://api.baselinker.com/connector.php", )
	header = fields.Text(default="HEADER", )
	parameters = fields.Text(default="PARAMETERS", )
	data = fields.Text(default="DATA", )
	result_code = fields.Char(default="RESULT_CODE", )
	result_msg = fields.Text(default="RESULT_MSG", )
	method = fields.Selection([
		('get','GET'),
		('put','PUT'),
		('post','POST'),
		('delete','DELETE'),
		('patch','PATCH')
		], store=True)
	model = fields.Char(default="res_model", )
	rec_id = fields.Integer(default="res_id", )
	
	def _prepare(self, name=None, 
		method='post', 
		direction='in', 
		state='draft',
		surl=None,
		header=None,
		parameters=None,
		data=None,
		result_code=None,
		result_msg=None,
		model=None,
		rec_id=0,
		baselinker_products_restore=False,
		baselinker_get_orders_by_status_id=None,
		baselinker_query_date=None,
		baselinker_delivery_product_id=None,
		baselinker_product_create_limit=2000,
		baselinker_product_update_limit=2000,
		baselinker_set_debug=False):
		return {
			'name': name,
			'method': method,
			'direction': direction,
			'state': state,
			'surl': surl,
			'header': header,
			'parameters': parameters,
			'data': data,
			'result_code': result_code,
			'result_msg': result_msg,
			'model': model,
			'baselinker_products_restore': baselinker_products_restore,
			'baselinker_get_orders_by_status_id': baselinker_get_orders_by_status_id,
			'rec_id': rec_id,
			'baselinker_query_date': baselinker_query_date,
			'baselinker_delivery_product_id': baselinker_delivery_product_id,
			'baselinker_product_create_limit': baselinker_product_create_limit,
			'baselinker_product_update_limit': baselinker_product_update_limit,
			'baselinker_set_debug': baselinker_set_debug,
		}
	
	def Init(self, config):
		result = True
		self.error = False
		self.login = config.get('baselinker_api_login')
		self.passwd = config.get('baselinker_api_passwd')
		self.tokenkey = config.get('baselinker_api_token_key')
		self.url = config.get('baselinker_api_url')
		self.inventory_id = config.get('baselinker_inventory_id')
		self.warehouse_id = config.get('baselinker_warehouse_id')
		self.baselinker_website_id = config.get('baselinker_website_id')
		self.baselinker_client_invitation = config.get('baselinker_client_invitation')
		self.baselinker_products_restore = config.get('baselinker_products_restore')
		self.connection_timeout = config.get('baselinker_api_connection_timeout')
		self.baselinker_query_date = config.get('baselinker_query_date')
		self.baselinker_get_unconfirmed_orders = config.get('baselinker_get_unconfirmed_orders')
		self.baselinker_get_external_invoices = config.get('baselinker_get_external_invoices')
		self.baselinker_get_orders_by_status_id = config.get('baselinker_get_orders_by_status_id')
		self.baselinker_delivery_product_id = config.get('baselinker_delivery_product_id')
		self.baselinker_product_create_limit = config.get('baselinker_product_create_limit')
		self.baselinker_product_update_limit = config.get('baselinker_product_update_limit')
		self.baselinker_set_debug = config.get('baselinker_set_debug')
		values = {
			'login': config.get('baselinker_api_login'),
			'passwd': config.get('baselinker_api_passwd'),
			'tokenkey': config.get('baselinker_api_token_key'),
			'url': config.get('baselinker_api_url'),
			'inventory_id': config.get('baselinker_inventory_id'),
			'warehouse_id': config.get('baselinker_warehouse_id'),
			'baselinker_website_id': config.get('baselinker_website_id'),
			'baselinker_client_invitation': config.get('baselinker_client_invitation'),
			'baselinker_products_restore': config.get('baselinker_products_restore'),
			'price_group_id': config.get('baselinker_price_group_id'),
			'error': result,
			'connection_timeout': config.get('baselinker_api_connection_timeout'),
			'baselinker_query_date': config.get('baselinker_query_date'),
			'baselinker_get_unconfirmed_orders': config.get('baselinker_get_unconfirmed_orders'),
			'baselinker_get_external_invoices': config.get('baselinker_get_external_invoices'),
			'baselinker_get_orders_by_status_id': config.get('baselinker_get_orders_by_status_id'),
			'baselinker_delivery_product_id': config.get('baselinker_delivery_product_id'),
			'baselinker_product_create_limit': config.get('baselinker_product_create_limit'),
			'baselinker_product_update_limit': config.get('baselinker_product_update_limit'),
			'baselinker_set_debug': config.get('baselinker_set_debug')
		}
		#self.write(values)
		if (self.error):
			result = False

		return result

	"""	REST API BaseLinker
		getInventoryIntegrations

	"""
	def getInventoryIntegrations(self, inventory_id):
		result = None
		data = {
			'method': 'getInventoryIntegrations',
			'parameters': json.dumps({
				'inventory_id': """%s""" % inventory_id,
			})
		}
		headers = { "X-BLToken": "%s" % self.tokenkey, "Content-type": "application/x-www-form-urlencoded" }
		url = self.url
		try:
			result = requests.post( url, data=data, headers=headers)
		except:
			result = None
		result_status_code = result_text = ""
		if hasattr(result, 'status_code'): result_status_code = result.status_code
		if hasattr(result, 'text'): result_text = result.text
		if result: state = 'sent'
		else: state = 'err'
		values = {
			'name': 'getInventoryIntegrations',
			'method': 'post',
			'direction': 'in',
			'state': state,
			'surl': url,
			'header': headers,
			'parameters': "",
			'data': data,
			'result_code': result_status_code,
			'result_msg': result_text,
			'model': self.model,
			'rec_id': self.rec_id
		}
		api = self.sudo().update(values)
		return result


	""" REST API BaseLinker
		getInventories

	"""
	def getInventories(self, offset=0, limit=100):
		result = None

		data = {
			'method': 'getInventories'
		}
		headers = { "X-BLToken": "%s" % self.tokenkey, "Content-type": "application/x-www-form-urlencoded" }
		url = self.url
		try:
			result = requests.post( url, data=data, headers=headers)
		except:
			result = None

		result_status_code = result_text = ""
		if hasattr(result, 'status_code'): result_status_code = result.status_code
		if hasattr(result, 'text'): result_text = result.text
		if result: state = 'sent'
		else: state = 'err'
		values = {
			'name': 'getInventories',
			'method': 'post',
			'direction': 'in',
			'state': state,
			'surl': url,
			'header': headers,
			'parameters': "",
			'data': data,
			'result_code': result_status_code,
			'result_msg': result_text,
			'model': self.model,
			'rec_id': self.rec_id
		}
		api = self.sudo().update(values)
		return result

	""" REST API BaseLinker
		getInventoryPriceGroups

	"""
	
	def getInventoryPriceGroups(self):
		result = None
		data = {
			'method': 'getInventoryPriceGroups'
		}
		headers = { "X-BLToken": "%s" % self.tokenkey, "Content-type": "application/x-www-form-urlencoded" }
		url = self.url
		try:
			result = requests.post( url, data=data, headers=headers)
		except:
			result = None

		result_status_code = result_text = ""
		if hasattr(result, 'status_code'): result_status_code = result.status_code
		if hasattr(result, 'text'): result_text = result.text
		if result: state = 'sent'
		else: state = 'err'
		values = {
			'name': 'getInventoryPriceGroups',
			'method': 'post',
			'direction': 'in',
			'state': state,
			'surl': url,
			'header': headers,
			'parameters': "",
			'data': data,
			'result_code': result_status_code,
			'result_msg': result_text,
			'model': self.model,
			'rec_id': self.rec_id
		}
		api = self.sudo().update(values)
		return result

	""" REST API BaseLinker
		getInventoryProductsData
	"""
	
	def getInventoryProductsData(self, product_id, inventory_id):
		result = None
		data = {
			'method': 'getInventoryProductsData',
			'parameters': json.dumps({
				'inventory_id': """%s""" % inventory_id,
				'products': product_id 
			})
		}
		headers = { "X-BLToken": "%s" % self.tokenkey, "Content-type": "application/x-www-form-urlencoded" }
		url = self.url
		try:
			result = requests.post( url, data=data, headers=headers)
		except:
			result = None

		result_status_code = result_text = ""
		if hasattr(result, 'status_code'): result_status_code = result.status_code
		if hasattr(result, 'text'): result_text = result.text
		if result: state = 'sent'
		else: state = 'err'
		values = {
			'name': 'getInventoryProductsData',
			'method': 'post',
			'direction': 'in',
			'state': state,
			'surl': url,
			'header': headers,
			'parameters': "",
			'data': data,
			'result_code': result_status_code,
			'result_msg': result_text,
			'model': self.model,
			'rec_id': self.rec_id
		}
		api = self.sudo().update(values)
		return result

	"""	REST API BaseLinker
		updateInventoryProductsStock
	"""	
	def updateInventoryProductsStock(self, inventory_id, products):
		result = None
		data = {
			'method': 'updateInventoryProductsStock',
			'parameters': json.dumps({
				'inventory_id': inventory_id,
				'products': products
			})
		}

		headers = { "X-BLToken": "%s" % self.tokenkey, "Content-type": "application/x-www-form-urlencoded" }
		url = self.url
		try:
			result = requests.post( url, data=data, headers=headers)
		except:
			result = None

		result_status_code = result_text = ""
		if hasattr(result, 'status_code'): result_status_code = result.status_code
		if hasattr(result, 'text'): result_text = result.text
		if result: state = 'sent'
		else: state = 'err'
		values = {
			'name': 'updateInventoryProductsStock',
			'method': 'post',
			'direction': 'out',
			'state': state,
			'surl': url,
			'header': headers,
			'parameters': "",
			'data': data,
			'result_code': result_status_code,
			'result_msg': result_text,
			'model': self.model,
			'rec_id': self.rec_id
		}
		api = self.sudo().update(values)
		return result


	"""	REST API BaseLinker
		getInventoryProductsStock
	"""	
	def getInventoryProductsStock(self, inventory_id, page=None):
		result = None
		data = {
			'method': 'getInventoryProductsStock',
			'parameters': json.dumps({
				'inventory_id': inventory_id
			})
		}
		if page:
			data = {
			'method': 'getInventoryProductsStock',
			'parameters': json.dumps({
				'inventory_id': inventory_id,
				'page': page
				})
			}

		headers = { "X-BLToken": "%s" % self.tokenkey, "Content-type": "application/x-www-form-urlencoded" }
		url = self.url
		try:
			result = requests.post( url, data=data, headers=headers)
		except:
			result = None

		result_status_code = result_text = ""
		if hasattr(result, 'status_code'): result_status_code = result.status_code
		if hasattr(result, 'text'): result_text = result.text
		if result: state = 'sent'
		else: state = 'err'
		values = {
			'name': 'getInventoryProductsStock',
			'method': 'post',
			'direction': 'in',
			'state': state,
			'surl': url,
			'header': headers,
			'parameters': "",
			'data': data,
			'result_code': result_status_code,
			'result_msg': result_text,
			'model': self.model,
			'rec_id': self.rec_id
		}
		api = self.sudo().update(values)
		return result


	"""	REST API BaseLinker
		getInventoryProductsList
	"""
	def getInventoryProductsList(self, inventory_id, page=None):
		result = None
		data = {
			'method': 'getInventoryProductsList',
			'parameters': json.dumps({
				'inventory_id': inventory_id,
				'filter_sort': 'id DESC',
			})
		}
		if page:
			data = {
			'method': 'getInventoryProductsList',
			'parameters': json.dumps({
				'inventory_id': inventory_id,
				'page': page,
				'filter_sort': 'id DESC',
				})
			}

		headers = { "X-BLToken": "%s" % self.tokenkey, "Content-type": "application/x-www-form-urlencoded" }
		url = self.url
		try:
			result = requests.post( url, data=data, headers=headers)
		except:
			result = None

		result_status_code = result_text = ""
		if hasattr(result, 'status_code'): result_status_code = result.status_code
		if hasattr(result, 'text'): result_text = result.text
		if result: state = 'sent'
		else: state = 'err'
		values = {
			'name': 'getInventoryProductsList',
			'method': 'post',
			'direction': 'in',
			'state': state,
			'surl': url,
			'header': headers,
			'parameters': "",
			'data': data,
			'result_code': result_status_code,
			'result_msg': result_text,
			'model': self.model,
			'rec_id': self.rec_id
		}
		api = self.sudo().update(values)
		return result

	""" REST API BaseLinker
		getInventoryWarehouses

	"""
	def getInventoryWarehouses(self):
		result = None
		data = {
			'method': 'getInventoryWarehouses'
		}
		headers = { "X-BLToken": "%s" % self.tokenkey, "Content-type": "application/x-www-form-urlencoded" }
		url = self.url
		try:
			result = requests.post( url, data=data, headers=headers)
		except:
			result = None

		result_status_code = result_text = ""
		if hasattr(result, 'status_code'): result_status_code = result.status_code
		if hasattr(result, 'text'): result_text = result.text
		if result: state = 'sent'
		else: state = 'err'
		values = {
			'name': 'getInventoryWarehouses',
			'method': 'post',
			'direction': 'in',
			'state': state,
			'surl': url,
			'header': headers,
			'parameters': "",
			'data': data,
			'result_code': result_status_code,
			'result_msg': result_text,
			'model': self.model,
			'rec_id': self.rec_id
		}
		api = self.sudo().update(values)
		return result

	""" REST API BaseLinker
		getExternalStoragesList

	"""
	def getExternalStoragesList(self, offset=0, limit=100):
		result = None

		data = {
			'method': 'getExternalStoragesList'
		}
		headers = { "X-BLToken": "%s" % self.tokenkey, "Content-type": "application/x-www-form-urlencoded" }
		url = self.url
		try:
			result = requests.post( url, data=data, headers=headers)
		except:
			result = None

		result_status_code = result_text = ""
		if hasattr(result, 'status_code'): result_status_code = result.status_code
		if hasattr(result, 'text'): result_text = result.text
		if result: state = 'sent'
		else: state = 'err'
		values = {
			'name': 'getExternalStoragesList',
			'method': 'post',
			'direction': 'in',
			'state': state,
			'surl': url,
			'header': headers,
			'parameters': "",
			'data': data,
			'result_code': result_status_code,
			'result_msg': result_text,
			'model': self.model,
			'rec_id': self.rec_id
		}
		api = self.sudo().update(values)
		return result

	""" REST API BaseLinker
		getExternalStorageProductsData

	"""
	def getExternalStorageProductsData(self, storage_id, products):
		result = None
		if not isinstance(products,list):
			 products = [ products ]
		data = {
			'method': 'getExternalStorageProductsData',
			'parameters': json.dumps({
				'storage_id': """%s""" % storage_id,
				'products': products
			})
		}
		headers = { "X-BLToken": "%s" % self.tokenkey, "Content-type": "application/x-www-form-urlencoded" }
		url = self.url
		try:
			result = requests.post( url, data=data, headers=headers)
		except:
			result = None

		result_status_code = result_text = ""
		if hasattr(result, 'status_code'): result_status_code = result.status_code
		if hasattr(result, 'text'): result_text = result.text
		if result: state = 'sent'
		else: state = 'err'
		values = {
			'name': 'getExternalStorageProductsData',
			'method': 'post',
			'direction': 'in',
			'state': state,
			'surl': url,
			'header': headers,
			'parameters': "",
			'data': data,
			'result_code': result_status_code,
			'result_msg': result_text,
			'model': self.model,
			'rec_id': self.rec_id
		}
		api = self.sudo().update(values)
		return result


	""" REST API BaseLinker
		getStoragesList

	"""
	def getStoragesList(self, offset=0, limit=100):
		result = None

		data = {
			'method': 'getStoragesList'
		}
		headers = { "X-BLToken": "%s" % self.tokenkey, "Content-type": "application/x-www-form-urlencoded" }
		url = self.url
		try:
			result = requests.post( url, data=data, headers=headers)
		except:
			result = None

		result_status_code = result_text = ""
		if hasattr(result, 'status_code'): result_status_code = result.status_code
		if hasattr(result, 'text'): result_text = result.text
		if result: state = 'sent'
		else: state = 'err'
		values = {
			'name': 'getStoragesList',
			'method': 'post',
			'direction': 'in',
			'state': state,
			'surl': url,
			'header': headers,
			'parameters': "",
			'data': data,
			'result_code': result_status_code,
			'result_msg': result_text,
			'model': self.model,
			'rec_id': self.rec_id
		}
		api = self.sudo().update(values)
		return result


	""" REST API BaseLinker
		addOrder

	"""	
	def addOrder(self, jdata):
		result = None
		data = {
			'X-BLToken': self.tokenkey,
			'method': 'addOrder',
			'parameters': json.dumps(jdata)
		}
		headers = { "X-BLToken": "%s" % self.tokenkey }
		url = self.url
		try:
			result = requests.post( url, data=data, headers=headers)
		except:
			result = None

		result_status_code = result_text = ""
		if hasattr(result, 'status_code'): result_status_code = result.status_code
		if hasattr(result, 'text'): result_text = result.text
		if result: state = 'sent'
		else: state = 'err'
		values = {
			'name': 'addOrder',
			'method': 'post',
			'direction': 'out',
			'state': state,
			'surl': url,
			'header': headers,
			'parameters': "",
			'data': data,
			'result_code': result_status_code,
			'result_msg': result_text,
			'model': self.model,
			'rec_id': self.rec_id
		}
		api = self.sudo().update(values)
		return result

	""" REST API BaseLinker
		addInventoryProduct

	"""
	def addInventoryProduct(self, jdata):
		result = None
		data = {
			'X-BLToken': self.tokenkey,
			'method': 'addInventoryProduct',
			'parameters': json.dumps(jdata)
		}
		headers = { "X-BLToken": "%s" % self.tokenkey }
		url = self.url
		try:
			result = requests.post( url, data=data, headers=headers)
		except:
			result = None

		result_status_code = result_text = ""
		if hasattr(result, 'status_code'): result_status_code = result.status_code
		if hasattr(result, 'text'): result_text = result.text
		if result: state = 'sent'
		else: state = 'err'
		values = {
			'name': 'addInventoryProduct',
			'method': 'post',
			'direction': 'out',
			'state': state,
			'surl': url,
			'header': headers,
			'parameters': "",
			'data': data,
			'result_code': result_status_code,
			'result_msg': result_text,
			'model': self.model,
			'rec_id': self.rec_id
		}
		api = self.sudo().update(values)
		return result

	""" REST API BaseLinker
		addProduct

		- urllib.parse.urlencode(jdata)
		
	"""
	def addProduct(self, jdata):
		result = None
		data = {
			'X-BLToken': self.tokenkey,
			'method': 'addProduct',
			'parameters': json.dumps(jdata)
		}
		headers = { "X-BLToken": "%s" % self.tokenkey}
		url = self.url
		try:
			result = requests.post( url, data=data, headers=headers)
		except:
			result = None

		result_status_code = result_text = ""
		if hasattr(result, 'status_code'): result_status_code = result.status_code
		if hasattr(result, 'text'): result_text = result.text
		if result: state = 'sent'
		else: state = 'err'
		values = {
			'name': 'addProduct',
			'method': 'post',
			'direction': 'out',
			'state': state,
			'surl': url,
			'header': headers,
			'parameters': "",
			'data': data,
			'result_code': result_status_code,
			'result_msg': result_text,
			'model': self.model,
			'rec_id': self.rec_id
		}
		api = self.sudo().update(values)
		return result

	""" REST API BaseLinker
		addCategory

		- urllib.parse.urlencode(jdata)
		
	"""
	def addCategory(self, jdata, mode='create'):
		result = None
		data = {
			'X-BLToken': self.tokenkey,
			'method': 'addCategory',
			'parameters': json.dumps(jdata)
		}
		headers = { "X-BLToken": "%s" % self.tokenkey}
		url = self.url
		try:
			result = requests.post( url, data=data, headers=headers)
		except:
			result = None

		result_status_code = result_text = ""
		if hasattr(result, 'status_code'): result_status_code = result.status_code
		if hasattr(result, 'text'): result_text = result.text
		if result: state = 'sent'
		else: state = 'err'
		if mode == 'create':
			name = 'addCategory'
		else:
			name = 'addCategory (update)'

		values = {
			'name': name,
			'method': 'post',
			'direction': 'out',
			'state': state,
			'surl': url,
			'header': headers,
			'parameters': "",
			'data': data,
			'result_code': result_status_code,
			'result_msg': result_text,
			'model': self.model,
			'rec_id': self.rec_id
		}
		api = self.sudo().update(values)
		return result

	""" REST API BaseLinker
		getLabels

	"""
	def getLabels(self,delivery_package_module, delivery_package_nr):
		result = None
		data = {
			'method': 'getLabel',
			'parameters': json.dumps({
				'courier_code': delivery_package_module,
				'package_number': delivery_package_nr
			})
		}
		headers = { "X-BLToken": "%s" % self.tokenkey, "Content-type": "application/x-www-form-urlencoded" }
		url = self.url

		try:
			result = requests.post( url, data=data, headers=headers)
			if result.status_code == 200:
				return json.loads(result.text)
		except:
			result = None

		return result

	""" REST API BaseLinker
		getOrderByID

	"""
	def getOrderByID(self, order=None):
		result = None
		config = self.env['res.config.settings'].sudo().get_values()
		get_unconfirmed_orders = config.get('baselinker_get_unconfirmed_orders') or False
		data = {
			'method': 'getOrders',
			'parameters': json.dumps({
				'order_id': order,
				'get_unconfirmed_orders': get_unconfirmed_orders
			})
		}		
		headers = { "X-BLToken": "%s" % self.tokenkey, "Content-type": "application/x-www-form-urlencoded" }
		url = self.url
		try:
			result = requests.post( url, data=data, headers=headers)
		except:
			result = None

		result_status_code = result_text = ""
		if hasattr(result, 'status_code'): result_status_code = result.status_code
		if hasattr(result, 'text'): result_text = result.text
		if result: state = 'sent'
		else: state = 'err'
		values = {
			'name': 'getOrderByID',
			'method': 'post',
			'direction': 'in',
			'state': state,
			'surl': url,
			'header': headers,
			'parameters': "",
			'data': data,
			'result_code': result_status_code,
			'result_msg': result_text,
			'model': self.model,
			'rec_id': self.rec_id
		}
		api = self.sudo().update(values)
		return result

	""" REST API BaseLinker
		getInvoices
	"""
	def getInvoices(self, byDate=None, offset=None, limit=None, invoice_id=None, order_id=None):
		result = None
		if not byDate:
			byDate = datetime( datetime.now().year, datetime.now().month, 1, 0, 0).strftime('%s') # this month

		config = self.env['res.config.settings'].sudo().get_values()
		get_external_invoices = config.get('baselinker_get_external_invoices') or False

		data = {
			'method': 'getInvoices',
			'parameters': json.dumps({
				'date_from': byDate,
			})
		}
		if get_external_invoices: data['parameters']['baselinker_get_external_invoices'] = True
		if invoice_id: data['parameters']['invoice_id'] = invoice_id
		if order_id: data['parameters']['order_id'] = order_id

		headers = { "X-BLToken": "%s" % self.tokenkey, "Content-type": "application/x-www-form-urlencoded" }
		url = self.url
		try:
			result = requests.post( url, data=data, headers=headers)
		except:
			result = None

		result_status_code = result_text = ""
		if hasattr(result, 'status_code'): result_status_code = result.status_code
		if hasattr(result, 'text'): result_text = result.text
		if result: state = 'sent'
		else: state = 'err'
		values = {
			'name': 'getInvoices',
			'method': 'post',
			'direction': 'in',
			'state': state,
			'surl': url,
			'header': headers,
			'parameters': "",
			'data': data,
			'result_code': result_status_code,
			'result_msg': result_text,
			'model': self.model,
			'rec_id': self.rec_id
		}
		api = self.sudo().update(values)
		return result


	""" REST API BaseLinker
		getOrders

	"""
	def getOrders(self, byDate=None, offset=None, limit=None ):
		result = None
		if not byDate:
			byDate = datetime( datetime.now().year, datetime.now().month, 1, 0, 0).strftime('%s') # this month

		config = self.env['res.config.settings'].sudo().get_values()
		baselinker_get_unconfirmed_orders = config.get('baselinker_get_unconfirmed_orders') or False
		status = config.get('baselinker_get_orders_by_status_id') or False
		if status:
			status_id = status.bl_id
		else:	
			status_id = False

		if status_id:
			data = {
			'method': 'getOrders',
			'parameters': json.dumps({
				'date_from': byDate,
				'get_unconfirmed_orders': baselinker_get_unconfirmed_orders,
				'status_id': status_id
			})
			}
		else:
			data = {
				'method': 'getOrders',
				'parameters': json.dumps({
					'date_from': byDate,
					'get_unconfirmed_orders': baselinker_get_unconfirmed_orders
				})
			}

		headers = { "X-BLToken": "%s" % self.tokenkey, "Content-type": "application/x-www-form-urlencoded" }
		url = self.url
		try:
			result = requests.post( url, data=data, headers=headers)
		except:
			result = None

		result_status_code = result_text = ""
		if hasattr(result, 'status_code'): result_status_code = result.status_code
		if hasattr(result, 'text'): result_text = result.text
		state = 'sent' if result else 'err'
		values = {
			'name': 'getOrders',
			'method': 'post',
			'direction': 'in',
			'state': state,
			'surl': url,
			'header': headers,
			'parameters': "",
			'data': data,
			'result_code': result_status_code,
			'result_msg': result_text,
			'model': self.model,
			'rec_id': self.rec_id
		}
		api = self.sudo().update(values)
		return result

	""" REST API BaseLinker
		setOrderFields
	"""
	def setOrderFields(self, order_id, admin_comments=None, parameters=None):
		result = None
		data = {
			'method': 'setOrderFields',
			'parameters': json.dumps({
				'order_id': order_id, 
				'admin_comments': admin_comments
			})
		}
		headers = { "X-BLToken": "%s" % self.tokenkey, "Content-type": "application/x-www-form-urlencoded" }
		url = self.url
		try:
			result = requests.post( url, data=data, headers=headers)
		except:
			result = None

		result_status_code = result_text = ""
		if hasattr(result, 'status_code'): result_status_code = result.status_code
		if hasattr(result, 'text'): result_text = result.text
		if result: state = 'sent'
		else: state = 'err'
		values = {
			'name': 'setOrderFields',
			'method': 'post',
			'direction': 'out',
			'state': state,
			'surl': url,
			'header': headers,
			'parameters': "",
			'data': data,
			'result_code': result_status_code,
			'result_msg': result_text,
			'model': self.model,
			'rec_id': self.rec_id
		}
		api = self.sudo().update(values)
		return result

	""" REST API BaseLinker
		setOrderProductFields

		order['order_id'], order_product_id=move.bl_order_product_id, location=move.location_id.name NEW -> location_id.barcode

	"""
	def setOrderProductFields(self, order_id, order_product_id=None, location=None):
		result = None
		data = {
			'method': 'setOrderProductFields',
			'parameters': json.dumps({
				'order_id': order_id, 
				'order_product_id': order_product_id,
				'location': location
			})
		}
		headers = { "X-BLToken": "%s" % self.tokenkey, "Content-type": "application/x-www-form-urlencoded" }
		url = self.url
		try:
			result = requests.post( url, data=data, headers=headers)
		except:
			result = None

		result_status_code = result_text = ""
		if hasattr(result, 'status_code'): result_status_code = result.status_code
		if hasattr(result, 'text'): result_text = result.text
		if result: state = 'sent'
		else: state = 'err'
		values = {
			'name': 'setOrderProductFields',
			'method': 'post',
			'direction': 'out',
			'state': state,
			'surl': url,
			'header': headers,
			'parameters': "",
			'data': data,
			'result_code': result_status_code,
			'result_msg': result_text,
			'model': self.model,
			'rec_id': self.rec_id
		}
		api = self.sudo().update(values)
		return result


	""" REST API BaseLinker
		setOrderStatus
	"""
	def setOrderStatus(self, order_id, status_id):
		result = None
		data = {
			'method': 'setOrderStatus',
			'parameters': json.dumps({
				'order_id': order_id,
				'status_id': status_id
			})
		}
		headers = { "X-BLToken": "%s" % self.tokenkey, "Content-type": "application/x-www-form-urlencoded" }
		url = self.url
		try:
			result = requests.post( url, data=data, headers=headers)
		except:
			result = None

		result_status_code = result_text = ""
		if hasattr(result, 'status_code'): result_status_code = result.status_code
		if hasattr(result, 'text'): result_text = result.text
		if result: state = 'sent'
		else: state = 'err'
		values = {
			'name': 'setOrderStatus',
			'method': 'post',
			'direction': 'out',
			'state': state,
			'surl': url,
			'header': headers,
			'parameters': "",
			'data': data,
			'result_code': result_status_code,
			'result_msg': result_text,
			'model': self.model,
			'rec_id': self.rec_id
		}
		api = self.sudo().update(values)
		return result





	""" REST API BaseLinker
		getOrderStatusList

	"""
	def getOrderStatusList(self, byDate=None):
		result = None
		data = {
			'method': 'getOrderStatusList'
		}
		headers = { "X-BLToken": "%s" % self.tokenkey, "Content-type": "application/x-www-form-urlencoded" }
		url = self.url
		try:
			result = requests.post( url, data=data, headers=headers)
		except:
			result = None

		result_status_code = result_text = ""
		if hasattr(result, 'status_code'): result_status_code = result.status_code
		if hasattr(result, 'text'): result_text = result.text
		if result: state = 'sent'
		else: state = 'err'
		values = {
			'name': 'getOrderStatusList',
			'method': 'post',
			'direction': 'in',
			'state': state,
			'surl': url,
			'header': headers,
			'parameters': "",
			'data': data,
			'result_code': result_status_code,
			'result_msg': result_text,
			'model': self.model,
			'rec_id': self.rec_id
		}
		api = self.sudo().update(values)
		return result

	""" REST API BaseLinker
		getOrderSourcesList

	"""
	def getOrderSourcesList(self, byDate=None):
		result = None

		data = {
			'method': 'getOrderSources'
		}
		headers = { "X-BLToken": "%s" % self.tokenkey, "Content-type": "application/x-www-form-urlencoded" }
		url = self.url
		try:
			result = requests.post( url, data=data, headers=headers)
		except:
			result = None

		result_status_code = result_text = ""
		if hasattr(result, 'status_code'): result_status_code = result.status_code
		if hasattr(result, 'text'): result_text = result.text
		if result: state = 'sent'
		else: state = 'err'
		values = {
			'name': 'getOrderSources',
			'method': 'post',
			'direction': 'in',
			'state': state,
			'surl': url,
			'header': headers,
			'parameters': "",
			'data': data,
			'result_code': result_status_code,
			'result_msg': result_text,
			'model': self.model,
			'rec_id': self.rec_id
		}
		api = self.sudo().update(values)
		return result


class BLinkerMethods(models.Model):
	_name = 'bl_methods'
	_description = 'An array of names of methods supported by the storage'

	name = fields.Char('Names of methods supported by the storage')

class BLinkerExternalStoragesList(models.Model):
	_name = 'bl_externalstorages'
	_description = 'External Storage List'

	storage_id = fields.Char('Storage ID', help='ID in format "[type:bl|shop|warehouse]_[id:int]" (e.g. "shop_2445")') 
	name = fields.Char('Name')
	methods = fields.Char('Methods')
	company_id = fields.Many2one('res.company', string='Company', default=lambda self: self.env.company)

	@api.model
	def getExternalStoragesList(self):
		result = response = None
		values = self.env['blapi']._prepare(model='baselinker')
		rest = self.env['blapi'].create(values)
		config = self.env['res.config.settings'].sudo().get_values()
		result = rest.Init(config)
		if result:
			storages = rest.getExternalStoragesList()
			if storages:
				storage = storages.json()
				for record in storage['storages']:
					# aktualizacja słownika
					ext_storage = self.env['bl_externalstorages'].search([('storage_id','=',record.get('storage_id') )])
					if not ext_storage:
						vals = {	
							'name': record.get('name'),
							'storage_id': record.get('storage_id'),
							'methods':  record.get('methods'),
						}
						self.env['bl_externalstorages'].sudo().create(vals)
					if config.get('baselinker_set_debug'): 
						_logger.info("""\nBASELINKER.getExternalStoragesList:: %s / %s""" % ( record.get('storage_id'), record.get('name')))


class BLinkerStoragesList(models.Model):
	_name = 'bl_storages'
	_description = 'Inventory Storage List'

	storage_id = fields.Char('Storage ID', help='ID in format "[type:bl|shop|warehouse]_[id:int]" (e.g. "shop_2445")')
	name = fields.Char('Name')
	methods = fields.Char('Methods')
	company_id = fields.Many2one('res.company', string='Company', default=lambda self: self.env.company)

	@api.model
	def getStoragesList(self):
		result = response = None
		values = self.env['blapi']._prepare(model='baselinker')
		rest = self.env['blapi'].create(values)
		config = self.env['res.config.settings'].sudo().get_values()
		result = rest.Init(config)
		if result:
			storages = rest.getStoragesList()
			if storages:
				storage = storages.json()
				for record in storage['storages']:
					# aktualizacja słownika
					ext_storage = self.env['bl_storages'].search([('storage_id','=',record.get('storage_id') )])
					if not ext_storage:
						vals = {
							'name': record.get('name'),
							'storage_id': record.get('storage_id'),
							'methods':  record.get('methods'),
						}
						self.env['bl_storages'].sudo().create(vals)
					if config.get('baselinker_set_debug'): 
						_logger.info("""\nBASELINKER.getStoragesList:: %s / %s""" % ( record.get('storage_id'), record.get('name')))



class BLinkerLinks(models.Model):
	_name = 'bl_links'
	_description = 'Links'

	name = fields.Char(related='bl_storage_id', readonly=True, string='Storage ID')
	storage_id = fields.Many2one('bl_storages')
	company_id = fields.Many2one('res.company', string='Company', default=lambda self: self.env.company)

	bl_storage_id = fields.Char('BL Storage ID', help='Storage ID in format "[type:bl|shop|warehouse]_[id:int]" (e.g. "shop_2445")') 
	bl_product_id = fields.Char('Product identifier in external warehouse')
	bl_variant_id = fields.Char('Product variant identifier in the external warehouse')

class BLinkerImages(models.Model):
	_name = 'bl_images'
	_description = 'An array of product images max 16'

	company_id = fields.Many2one('res.company', string='Company', default=lambda self: self.env.company)

	image_type = fields.Selection([('url','URL'),('data','Data')],'Image Format', default='url')
	image_base64 = fields.Char()
	image_url = fields.Char()


"""
"""
class BLinkerText_Fields(models.Model):
	_name = 'bl_text_fields'
	_description = 'BLinkerText_Fields'

	company_id = fields.Many2one('res.company', string='Company', default=lambda self: self.env.company)

	key = fields.Char()
	value = fields.Char()


"""
	(getOrderStatusList)
"""
class BaseLinkerStatus(models.Model):
	_name = 'baselinker.status'
	_description = 'BaseLinker Order Status'

	company_id = fields.Many2one('res.company', string='Company', default=lambda self: self.env.company)

	bl_id = fields.Integer('status identifier')
	name = fields.Char('status name')
	name_for_customer = fields.Char('long status name')
	color = fields.Char('status color in hex')

	state = fields.Selection([
		('draft', 'Robocze'),
		('done', 'Wykonane'),
		('cancel', 'Anulowano'),
		('error', 'Błąd'),
		('sale', 'Zamówienie sprzedaży'),
		('sent', 'Wysłane')
	], string='Dst Status', copy=False, index=True, store=True, default='draft', )

	status = fields.Selection([
		('new','Nowe'),
		('draft','Niezarejestrowano'),
		('registered','Zarejestrowany'),
		('err','Błąd'),
		('updated','Zaktualizowany'),
		('import','Do Importu'),
		('ready','Do Realizacji'),
		('waiting','Czeka na dostawę'),
		('cancel','Anulowane') ], string = 'BaseLinker Status', default='new', store=True, )


	"""
		(getOrderStatusList)
	"""
	def getOrderStatusList(self):
		baselinker = self.env['baselinker']
		result = baselinker.getOrderStatusList()
		if result:
			msg = json.dumps(result.json(), indent=4, sort_keys=True)
			response = result.json()
			if response['status'] == 'SUCCESS':
				for state in response['statuses']:
					oid = state['id']
					bstatus = self.env['baselinker.status'].search([('bl_id','=',oid)])
					if not bstatus:
						name = state['name']
						values = {
							'bl_id': oid,
							'name': name,
							'name_for_customer': state['name_for_customer'],
							'color': state['color'],
						}
						bstatus = self.env['baselinker.status']
						bstatus.sudo().create(values)


class BaseLinkerInventoryWarehouses(models.Model):
	_name = 'bl_inventorywarehouses'
	_description = 'List of warehouses available in BaseLinker catalogues'

	name = fields.Char('Warehouse name', default="/", )
	warehouse_type = fields.Selection([('bl','BaseLinker'),('shop','Shop'),('warehouse','Warehouse'),('other','Other')],string='Type',default='bl')
	warehouse_id = fields.Integer('Warehouse ID', default=0, )
	description = fields.Char('Description', default="/" )
	stock_edition = fields.Boolean('Is manual stock editing permitted', default=False, )
	is_default = fields.Boolean('Default warehouse', default=False, )
	stock_location_id = fields.Many2one('stock.location')


class BaseLinkerInventories(models.Model):
	_name = 'bl_inventories'
	_inherit = [ 'mail.thread', 'mail.activity.mixin', 'portal.mixin']
	_description = "Catalogs available in the BaseLinker storage"

	company_id = fields.Many2one('res.company', string='Company', default=lambda self: self.env.company)
	inventory_id = fields.Char('ID of a catalog')
	name = fields.Char('Catalog name')
	description = fields.Text('Catalog description')
	languages = fields.Char('An array of languages available')
	default_language = fields.Char('Default language')
	price_groups = fields.Char('An array of price groups IDs available in the catalog')
	default_price_group = fields.Integer('ID of the price group default for the catalog')
	warehouses = fields.Char('An array of warehouse IDs available in the catalog')
	default_warehouse = fields.Char('ID of the warehouse default for the catalog')
	reservations = fields.Boolean('Does this catalogue support reservations')
	is_default = fields.Boolean('Is this catalog a default catalog')
	in_use = fields.Boolean('Used in synschronisation', default=False, store=True, )
	number = fields.Integer('Number of Records')

	supplier_ids = fields.Many2many('res.partner', string='Supplier')
	route_ids = fields.Many2many('stock.location.route')

	state = fields.Selection([
		('idle','Idle'),
		('product','Products Update'),
		('stock','Stock Update'),
		('busy','Busy'),
		('create_required','Resume required'),
		('update_required','Update required'),
	],default='idle', store=True, )


	def reset_idle(self):
		for inventory in self:
			inventory.state = 'idle'

	def bl_synchronization_state(self, state):
		query = """UPDATE """
		query += """ir_config_parameter SET value = '%s' WHERE key = 'bl_order_sync_state' """ % (state)
		self.env.cr.execute(query)
		self.env.cr.commit()
		self.env['ir.config_parameter'].sudo().set_param('bl_order_sync_state',state)

	@api.model
	def getInventoryProductsStock(self):
		start_timer = time.time()
		config = self.env['res.config.settings'].sudo().get_values()
		counter = 0
		stock_messages = []
		# Przygotowanie bloku odpowiedzi z BaseLinker'a
		for inventory in self:
			query = """UPDATE bl_inventories SET state = 'stock' WHERE id = '%s'""" % self.id
			self.env.cr.execute(query)
			self.env.cr.commit()
			# API CALL
			baselinker = self.env['baselinker']
			# max 1000 records per result
			current = 0
			page = 1
			page_size = 1000
			read = True
			messages = []
			last_status = 'UNKNOWN'
			while read:
				response = baselinker.getInventoryProductsStock( inventory_id=inventory.inventory_id, page=page)
				last_status = response.get('status') or 'UNKNOWN'
				if response and ('SUCCESS' in response.get('status')):
					results = response
					recno = 0
					for message in results['products']:
						current += 1
						messages.append(results['products'][message])
						recno += 1
					if current >= (page * page_size): page += 1
					if recno == 0 or recno < page_size:
						break
				else:
					break
				if read != True:
					if config.get('baselinker_set_debug'): 
						_logger.info("""\n====>>> getInventoryProductsStock[%s]: RESULT BREAK %s""" % ( inventory.inventory_id, read ))
					break
			for stock in messages:
				stock_messages.append(stock)

			if config.get('baselinker_set_debug'): 
				_logger.info("""\n====>>> getInventoryProductsStock: \n%s""" % ( messages ))

		# Przetworzenie bloku odpowiedzi w blok danych Odoo do aktualizacji stoku
		if config.get('baselinker_set_debug'): 
			_logger.info("""\n====>>> getInventoryProductsStock: ALL INVENTORY %s / ELEMENTS %s""" % ( inventory.name, len(stock_messages) ))
		stock2update = []
		products2update = []
		for data in stock_messages:
			stock = data.get('stock')
			reservation = data.get('reservation')
			variants = data.get('variants')
			product_id = data.get('product_id')
			location = quantity = 0
			
			#for jproduct in stock:
			#	location = jproduct
			#	quantity = stock[jproduct]
			#products2update.append(product_id)
			if variants:
				for variant in variants:
					jvariant = variants[variant]
					location = quantity = quantity = 0
					for location in jvariant:
						quantity = jvariant[location]
						location_id = self.get_stock_location_id(location)
						value = { 'product_id': product_id, 'variant_id': variant, 'location_id': location_id, 'quantity': quantity, 'location': location}
						stock2update.append(value)
					products2update.append(variant)
				#
			else:
				#no variants
				products2update.append(product_id)
				product_id = data.get('product_id')
				for jlocation in stock:
					location_id = self.get_stock_location_id(jlocation)
					quantity = stock[jlocation]
					value = { 'product_id': product_id, 'variant_id': product_id, 'location_id': location_id, 'quantity': quantity }
					stock2update.append(value)
		#return None
		start_timer = time.time()
		# Aktualizacja kwantów
		# reserved_quantity ?
		#for e in stock2update: _logger.info("""\n====>>> %s""" % ( e )); return None
		domain = [('bl_variant_id','in', products2update)]
		products = self.env['product.product'].sudo().search(domain)
		stock_quants = self.env['stock.quant'].sudo()
		sleeper = 1
		for row in stock2update:
			if sleeper == 1000: 
				if config.get('baselinker_set_debug'): _logger.info("""\n==> still working stock2update ...""" ) 
				sleeper = 0
			sleeper += 1
			pid = row.get('product_id')
			vid = row.get('variant_id')
			if row.get('product_id') == row.get('variant_id'):
				domain = [('bl_product_id','=', '%s' % pid)]
			else:
				domain = [('bl_product_id','=', '%s' % pid), ('bl_variant_id','=', '%s' % vid)]
			quantity = row.get('quantity')
			#product = products.filtered_domain(domain)
			product = products.search(domain)

			if not product: 
				if config.get('baselinker_set_debug'): 
					_logger.info("""\nNOT PRODUCT => %s / %s""" % ( pid, vid ) )
				continue
			location_id = row.get('location_id') #self.get_stock_location_id( row.get('location'))
			if not location_id:
				if config.get('baselinker_set_debug'):
					_logger.info("""\nNOT LOCATION => %s / %s""" % ( location_id, row ))
				continue

			#inventory_quant = stock_quants.filtered(lambda r: ( (r.location_id==location_id.id) and (r.product_id==product.id)))
			inventory_quant = stock_quants.search([('location_id','=',location_id.id),('product_id','=', product.id)])
			if not inventory_quant:
				inventory_quant = self.env['stock.quant'].create({
					'product_id': product.id,
					'location_id': location_id.id,
					'quantity': 0,
					'inventory_quantity': 0
				})
			else:
				inventory_quant.write({'inventory_quantity': 0, 'quantity': 0})
			if inventory_quant.quantity == quantity: 
				continue
			if quantity == 0: 
				continue
			inventory_quant.write({'inventory_quantity': quantity, 'quantity': quantity})
			#inventory_quant._update_available_quantity( product, location_id, quantity )
			inventory_quant.action_apply_inventory()

		end_timer = time.time()
		result_timer = round( ((end_timer - start_timer) ) / 60, 2 )
		if config.get('baselinker_set_debug'): 
			_logger.info("""\nDONE.stock2update => rows = %s IN %s min""" % ( len(stock2update), result_timer ) )

		colour = "#00223d"
		msg = """<b style='color: %s;'>Aktualizacja Kwantów: %s (%s min)</b> """ % ( colour, len(stock2update), result_timer)
		inventory.message_post(body=msg)
		inventory.state = 'idle'

		return None

	"""
		updateInventoryProductsStock
		The method allows to update stocks of products (and/or their variants) in BaseLinker catalog. Maximum 1000 products at a time.
	"""
	def updateInventoryProductsStock(self):
		config = self.env['res.config.settings'].sudo().get_values()
		if config.get('baselinker_set_debug'): 
			_logger.info("""\nSTART.updateInventoryProductsStock""")
		start_timer = time.time()
		config = self.env['res.config.settings'].sudo().get_values()
		counter = 0
		stock_messages = []
		# Przygotowanie bloku danych dla BaseLinker'a
		for inventory in self:
			query = """UPDATE bl_inventories SET state = 'stock' WHERE id = '%s'""" % inventory.id
			self.env.cr.execute(query)
			self.env.cr.commit()
			# API CALL
			baselinker = self.env['baselinker']
			# max 1000 records per result
			recno = current = 0
			limit = 1000
			messages = {}
			
			last_status = 'UNKNOWN'
			domain = [('bl_inventory_id','=', inventory.inventory_id)]
			products = self.env['product.product'].sudo().search( domain, order='id')
			stock_quants = self.env['stock.quant'].sudo()
			for product in products:
				if product.bl_variant_id:
					stock = {}
					for wh in ast.literal_eval(inventory.warehouses):
						location_id = self.get_stock_location_id( wh)
						quant = stock_quants.search([('location_id','=',location_id.id),('product_id','=', product.id)])
						if quant:
							qty = quant.quantity
						else:
							qty = 0
						stock[wh] = qty
					messages[product.bl_variant_id] = stock
					recno += 1
					if recno >= limit:
						recno = 0
						response = baselinker.updateInventoryProductsStock( inventory.inventory_id, messages)
						if config.get('baselinker_set_debug'): 
							_logger.info("""\n-POZOSTAŁO DO AKTUALIZACJI %s z %s""" % ( len(messages), len(products)))
						messages = {}
			if messages != {}:
				if config.get('baselinker_set_debug'): 
					_logger.info("""\n_POZOSTAŁO DO AKTUALIZACJI %s z %s""" % ( len(messages), len(products)))
				response = baselinker.updateInventoryProductsStock( inventory.inventory_id, messages)
				if response and (('ERROR' in response.get('status') or response.get('warnings') )):
					if self.env['ir.config_parameter'].sudo().get_param('baselinker_set_debug'): _logger.info("""\nBŁĄD AKTUALIZACJI PODCZAS %s""" % response)

		end_timer = time.time()
		result_timer = round( ((end_timer - start_timer)) / 60, 4 )
		if config.get('baselinker_set_debug'): 
			_logger.info("""\nupdateInventoryProductsStock Done => %s min""" % ( result_timer ) )
		inventory.state = 'idle'
		return None


	def get_stock_location_id(self, location):
		result = None
		if location:
			splited = location.split('_')
			if isinstance(splited, list):
				prefix = splited[0]
				suffix = splited[1]
				bl_inventorywarehouses = self.env['bl_inventorywarehouses'].sudo().search([('warehouse_type','=', prefix),('warehouse_id','=', suffix)])
				if bl_inventorywarehouses:
					result = bl_inventorywarehouses.stock_location_id
		return result

	def lock_synchronization(self):
		result = False
		if self.env['ir.config_parameter'].sudo().get_param('bl_order_sync_state') in ['busy','error']:
			msg = """\nODOO::\nTrwa obsługa priotytetowa zamówień.\nŻądanie aktualizacji zostaje przerwane.\n\nPonów żądanie gdy będzie to możliwe."""
			raise UserError(msg)
		try:
			query = """SELECT * FROM ir_config_parameter WHERE key = 'bl_order_sync_state';"""
			self.env.cr.execute(query)
			self.env.cr.commit()
			rows = self.env.cr.dictfetchall()
			if rows:
				for row in rows:
					if row.get('value') in ['busy','error']:
						msg = """\nSQL::\nTrwa obsługa priotytetowa zamówień.\nŻądanie aktualizacji zostaje przerwane.\n\nPonów żądanie gdy będzie to możliwe."""
						raise UserError(msg)
			result = True
		except Exception as e:
			msg = """\nEnvSQL ERROR::\n\n%s""" % e
			raise UserError(msg)

		self.bl_synchronization_state('busy')
		return result

	def manual_lock_synchronization(self):
		if self.env['ir.config_parameter'].sudo().get_param('bl_order_sync_state') in ['busy','error']:
			msg = """\nODOO::\nTrwa obsługa priotytetowa zamówień.\nŻądanie aktualizacji zostaje przerwane.\n\nPonów żądanie gdy będzie to możliwe.""" 
			raise UserError(msg)
		query = """SELECT * FROM ir_config_parameter WHERE key = 'bl_order_sync_state';"""
		self.env.cr.execute(query)
		self.env.cr.commit()
		rows = self.env.cr.dictfetchall()
		if rows:
			for row in rows:
				if row.get('value') in ['busy','error']:
					msg = """\nSQL::\nTrwa obsługa priotytetowa zamówień.\nŻądanie aktualizacji zostaje przerwane.\n\nPonów żądanie gdy będzie to możliwe."""
					raise UserError(msg)
		self.bl_synchronization_state('busy')


	def sync_product_stock_up(self):
		self.manual_lock_synchronization()
		context = dict(self._context or {})
		for catalog in self:
			if catalog.inventory_id:
				catalog.updateInventoryProductsStock()
		self.env['ir.config_parameter'].sudo().set_param('bl_order_sync_state','idle')

	def sync_product_stock(self):
		self.manual_lock_synchronization()
		context = dict(self._context or {})
		for catalog in self:
			if catalog.inventory_id:
				catalog.getInventoryProductsStock()
		self.env['ir.config_parameter'].sudo().set_param('bl_order_sync_state','idle')

	def cron_sync_products_stock(self):
		if not self.lock_synchronization(): return
		inventories = self.env['bl_inventories'].search([('in_use','=',True)])
		for inventory in inventories:
			inventory.getInventoryProductsStock()
		self.env['ir.config_parameter'].sudo().set_param('bl_order_sync_state','idle')

	def cron_sync_products_stock_up(self):
		if not self.lock_synchronization(): return
		inventories = self.env['bl_inventories'].search([('in_use','=',True)])
		for inventory in inventories:
			inventory.updateInventoryProductsStock()
		self.env['ir.config_parameter'].sudo().set_param('bl_order_sync_state','idle')

	def sync_product_catalog(self):
		self.manual_lock_synchronization()
		context = dict(self._context or {})
		for catalog in self:
			if catalog.inventory_id:
				result = self.env['product.product'].getInventoryProductsList( catalog.inventory_id)
				colour = "#00223d"
				msg = """<b style='color: %s;'>Aktualizacja Produktów: %s</b> """ % ( colour, result)
				catalog.message_post(body=msg)
		self.env['ir.config_parameter'].sudo().set_param('bl_order_sync_state','idle')
		return None


	def CreateProductWithVariants(self, product_id=None, product=None, bl_attr=None, inventory=None, supplier_ids=None, route_ids=None):
		created = 0
		res = None
		colour = "#113d09"
		pname = product.get('text_fields').get('name')
		is_bundle = product.get('is_bundle')
		###
		if pname in [None, False, ""]:
			pname = "BASELINKER PRODUCT ID: %s" % product
		prices = product.get('prices')
		list_price = 0
		for price in prices: list_price = prices[price]
		product_values = {
			'name': pname,
			'list_price': list_price,
			'weight': product.get('weight'),
			'description': product.get('text_fields').get('description'),
			'standard_price': product.get('average_cost'),
			'type': 'product',
		}
		if route_ids:
			product_values['route_ids'] = [(6, 0, route_ids)]

		variant_values = []
		if product.get('variants'):
			variants = product.get('variants')
			if variants: 
				value_ids = []
			else:
				value_ids = None
			for variant in variants:
				bl_attr_value = self.env['product.attribute.value'].sudo().search([('name','=',variant)])
				if not bl_attr_value:
					bl_attr_value = self.env['product.attribute.value'].create({'name': variant, 'attribute_id': bl_attr.id})
				prices = variants[variant].get('prices')
				for price in prices: list_price = prices[price]
				variant_values.append({
					'name': variants[variant].get('name') or variant,
					'bl_name': variants[variant].get('name'),
					'default_code': variants[variant].get('sku'),
					'bl_sku': variants[variant].get('sku'),
					'bl_ean': variants[variant].get('ean'),
					'bl_product_id': product_id,
					'bl_variant_id': variant,
					'lst_price': list_price,
					'bl_price': list_price,
					'bl_attr_value': bl_attr_value.id,
					'bl_inventory_id': inventory.inventory_id,
					'weight': variants[variant].get('weight'),
					'bl_height': variants[variant].get('height'),
					'bl_width':  variants[variant].get('width'),
					'bl_length': variants[variant].get('length'),
					'description': product.get('text_fields').get('description'),
					'bl_is_bundle': is_bundle,
				})
				value_ids.append( bl_attr_value.id )
			# dodanie listy atrybutów dla tworzonych wariantów
			product_values['attribute_line_ids'] = [(0, 0, { 
							'attribute_id': bl_attr.id, 
							'value_ids': [(6, 0, value_ids)],
			}) ]
		# UTWORZENIE REKORDU SZABLONU PRODUCT.TEMPLATE
		template = self.env['product.template'].sudo().create(product_values)
		colour = "#d61c1c"
		msg = json.dumps( product, indent=4, sort_keys=True)
		msg = """<b style='color: %s;'><pre>%s</pre> </b> """ % ( colour, msg.replace("\n","<br>"))
		template.message_post(body=msg)
		if supplier_ids:
			if self.env['ir.config_parameter'].sudo().get_param('baselinker_set_debug'):
				_logger.info("""\n\n supplier_ids = %s """ % supplier_ids)
			value_ids = []
			for supplier in supplier_ids:
				value_ids.append( (0, 0, {
					'name': supplier.id, 
					'product_code': pname, 
					'product_tmpl_id': template.id,
					'min_qty': 0,
					'price': 0,
					'currency_id':self.company_id.currency_id.id,
					'delay': 1,
					'company_id': self.company_id.id 
				}) )
				values = {
					'name': supplier.id,
					'product_code': pname,
					'product_tmpl_id': template.id,
					'min_qty': 0,
					'price': 0,
					'currency_id':self.company_id.currency_id.id,
					'delay': 1,
					'company_id': self.company_id.id
				}
				self.env['product.supplierinfo'].sudo().create(values)
			value = {'seller_ids': [ value_ids] }
			#ret = template.sudo().write(value)
			query = "INSERT INTO product_supplierinfo "
			#self.env.cr.execute(query)

		template.product_variant_ids[0].bl_inventory_catalog_id = inventory.id
		images = product.get('images')
		#self.from_baselinker_images_update( baselinker, template, images)
		# aktualizacja danych wariantów
		for variant in template.product_variant_ids:
			created += 1
			if variant_values:
				for vval in variant_values:
					vs = vval.get('bl_variant_id')
					vv = variant.bl_variant_id
					match = False
					for attr in variant.product_template_attribute_value_ids:
						if vs in attr.name:
							match = True
							break
					if match:
						value = {
							'bl_name': vval.get('name'),
							'bl_variant_id': vval.get('bl_variant_id'),
							'bl_product_id': product_id,
							'default_code': vval.get('default_code'),
							'bl_sku':  vval.get('bl_sku'),
							'bl_ean':  vval.get('bl_ean'),
							'bl_inventory_id': inventory.inventory_id,
							'bl_inventory_catalog_id': inventory.id,
							'bl_status': 'registered',
							'lst_price': vval.get('lst_price'),
							'bl_price': vval.get('bl_price'),
							'description': product.get('text_fields').get('description'),
							'bl_height': vval.get('bl_height'),
							'bl_width': vval.get('bl_width'),
							'bl_length': vval.get('bl_length'),
							'bl_is_bundle': is_bundle,
						}
						res = variant.sudo().write(value)
						msg = "%s" % vval
						msg = """<b style='color: %s;'>Utworzono: %s </b> """ % ( colour, msg.replace("\n","<br>"))
						variant.message_post(body=msg)
						break
			else:
				value = {
					'bl_category_id': product.get('category_id'),
					'bl_name': "%s" % product.get('text_fields').get('name'),
					'default_code': product.get('default_code'),
					'bl_sku': product.get('sku'),
					'bl_ean': product.get('ean'),
					'bl_product_id': product_id,
					'bl_variant_id' : product_id,
					'bl_inventory_id': inventory.inventory_id,
					'bl_status': 'registered',
					'bl_inventory_catalog_id': inventory.id,
					'description': product.get('text_fields').get('description'),
					'bl_height': product.get('height'),
					'bl_width': product.get('width'),
					'bl_length': product.get('length'),
					'bl_is_bundle': is_bundle,
				}
				res = variant.sudo().write(value)
				msg = "Registered from Baselinker" #json.dumps( product, indent=4, sort_keys=True)
				msg = """<b style='color: %s;'>%s </b> """ % ( colour, msg.replace("\n","<br>"))
				variant.message_post(body=msg)
		

		return created

	def updateProcuct(self, inventory, products):
		config = self.env['res.config.settings'].sudo().get_values()
		if config.get('baselinker_set_debug'): 
			_logger.info("""--------------------> updateProcuct [ inventory = %s / products = %s ]""" % (inventory, len(products)))
		products_ids = []
		for product in products:
			products_ids.append(product)
		bl_attr = self.env['product.attribute'].sudo().search([('name','=','baselinker')])
		if not bl_attr:
			bl_attr = self.env['product.attribute'].sudo().create({'name': 'baselinker'})

		created = updated = 0
		domain = [('bl_inventory_id','=', inventory.inventory_id ),('bl_product_id','in',products_ids)]
		base_result = self.env['product.product'].sudo().search(domain)

		if inventory.supplier_ids:
			supplier_ids = inventory.supplier_ids
		else:
			supplier_ids = None

		if inventory.route_ids:
			route_ids = inventory.route_ids.ids
		else:
			route_ids = None

		n = 0
		for product in products:
			n += 1
			# wykluczenie zestawów jeśli tak wybrano
			is_bundle = products[product].get('is_bundle')
			if config.get('baselinker_skip_bundle') and is_bundle:
				continue
			# wykluczenie istniejących
			break_for = False
			for stored_product in base_result:
				if product in stored_product.bl_product_id:
					break_for = True
					continue
			if break_for:
				updated += 1
				continue
			# CREATE
			res = self.CreateProductWithVariants( 
					product_id=product, 
					product=products[product], 
					bl_attr=bl_attr, 
					inventory=inventory, 
					supplier_ids=supplier_ids,
					route_ids=route_ids
			)
			created += res
			if config.get('baselinker_set_debug'): 
				_logger.info("""--> CREATED [ %s | %s / %s ]""" % (created, n, len(products)))
		return created, updated

	"""	004 BaseLinker Synchro Get Products

		on CRON		
	"""
	def cron_download_inventories_products(self):
		if not self.lock_synchronization(): return
		start_timer = time.time()
		config = self.env['res.config.settings'].sudo().get_values()
		context = dict(self._context or {})
		counter = 0
		if config.get('baselinker_set_debug'): 
			_logger.info("""\n====>>> download_inventories_products: START PROCESS with/%s""" % self)
		domain = [('in_use','=', True)]
		inventories = self.env['bl_inventories'].sudo().search(domain)
		baselinker_product_create_limit = config.get('baselinker_product_create_limit')
		baselinker_product_update_limit = config.get('baselinker_product_update_limit')
		products_created = products_updated = 0
		for inventory in inventories:
			products_created, products_updated = inventory.download_inventory_products()
			if products_created > baselinker_product_create_limit:
				inventory.state = 'create_required'
				if config.get('baselinker_set_debug'): 
					_logger.info("""\nX. download_inventories_products.LIMITS EXCEEDED: created[%s] updated[%s]""" % (products_created, products_updated) )
				break
		end_timer = time.time()
		result_timer = round( ((end_timer - start_timer)) / 1 )
		if config.get('baselinker_set_debug'): 
			_logger.info("""\n====>>> download_inventories_products.RESULT:TIMER: %s """ % result_timer)
		self.env['ir.config_parameter'].sudo().set_param('bl_order_sync_state','idle')

	""" 004.image BaseLinker Synchro Get Products Images

		on CRON
	"""
	def cron_download_inventories_products_image(self):
		###self.env['ir.config_parameter'].sudo().set_param('bl_order_sync_state','idle')
		if not self.lock_synchronization(): return
		start_timer = time.time()
		config = self.env['res.config.settings'].sudo().get_values()
		context = dict(self._context or {})
		counter = 0
		if config.get('baselinker_set_debug'):
			_logger.info("""\n====>>> CRON.download_inventories_products_image: START PROCESS with/%s""" % self)
		domain = [('in_use','=', True)]
		inventories = self.env['bl_inventories'].sudo().search(domain)
		baselinker_product_create_limit = config.get('baselinker_product_create_limit')
		baselinker_product_update_limit = config.get('baselinker_product_update_limit')
		products_created = products_updated = 0
		for inventory in inventories:
			products_updated = inventory.download_inventory_products_image()
			if products_created > baselinker_product_create_limit:
				inventory.state = 'create_required'
				if config.get('baselinker_set_debug'):
					_logger.info("""\nCRON.download_inventories_products_image.LIMITS EXCEEDED: created[%s] updated[%s]""" % (products_created, products_updated) )
				break
		end_timer = time.time()
		result_timer = round( ((end_timer - start_timer)) / 1 )
		if config.get('baselinker_set_debug'):
			_logger.info("""\n====>>> CRON.download_inventories_products_image.RESULT:TIMER: %s """ % result_timer)
		self.env['ir.config_parameter'].sudo().set_param('bl_order_sync_state','idle')

	"""
		for 004.image BaseLinker Synchro Get Products Images
	"""
	def download_inventory_products_image(self):
		products_updated = 0
		start_timer = time.time()
		config = self.env['res.config.settings'].sudo().get_values()
		baselinker_product_update_limit = config.get('baselinker_product_update_limit')
		context = dict(self._context or {})
		counter = 0
		if config.get('baselinker_set_debug'): 
			_logger.info("""\n====>>> INT.download_inventory_products_image: START PROCESS with/%s""" % self)
		baselinker_product_create_limit = config.get('baselinker_product_create_limit')
		baselinker_product_update_limit = config.get('baselinker_product_update_limit')
		# PREPARE PRODUCTS LIST
		status = 'idle'
		domain = [('bl_product_id','!=',False),('bl_inventory_id','=',self.inventory_id)]
		products = self.env['product.product'].sudo().search(domain)
		# BASELINKER API CALL
		baselinker = self.env['baselinker']
		for product in products:
			product_template = product.product_tmpl_id
			if product_template.product_template_image_ids:
				continue
			mail_messages = self.env['mail.message'].search([('model','=','product.template'),('res_id','=', product_template.id)],order='id desc',limit=1)
			if mail_messages:
				str_images = ""
				for mail_message in mail_messages:
					s = """%s""" % mail_message.body
					#s = s.replace("<br>","").replace("<b>","").replace("</b>","").replace("<pre>","").replace("</pre>","").replace("\n","").replace("<p>","").replace("</p>","").replace('<b style="color:#d61c1c">',"")
					s = s.replace("""<b style="color:#d61c1c"><pre>""","").replace("</pre> </b>","").replace("<br>","")
					try:
						j = json.loads(s)
						img = j.get('images')
						if img:
							str_images = json.dumps(img)
					except:
						_logger.error("""\n====>>> INT.download_inventory_products_image.JSON:S:\n%s """ % s)
						str_images = ""
					
				if str_images != "":
					product.from_baselinker_images_update(baselinker, product_template, img)
					products_updated += 1
				if products_updated >= baselinker_product_update_limit:
					break
			#XXXXXXXXX
		end_timer = time.time()
		result_timer = round( ((end_timer - start_timer)) / 1 )
		if config.get('baselinker_set_debug'): 
			_logger.info("""\n====>>> INT.download_inventory_products_image.RESULT:TIMER: %s """ % result_timer)
		return products_updated


	"""
		getInventoryProductsList > products_list -> getInventoryProductsData => updateProcuct|createProduct
		
	"""
	def download_inventory_products(self):
		start_timer = time.time()
		config = self.env['res.config.settings'].sudo().get_values()
		context = dict(self._context or {})
		counter = 0
		if config.get('baselinker_set_debug'): 
			_logger.info("""\n====>>> download_inventory_products: START PROCESS with/%s""" % self)
		inventory_number = 0
		baselinker_product_create_limit = config.get('baselinker_product_create_limit')
		baselinker_product_update_limit = config.get('baselinker_product_update_limit')
		status = 'idle'
		for inventory in self:
			query = """UPDATE bl_inventories SET state = 'product' WHERE id = '%s'""" % inventory.id
			self.env.cr.execute(query)
			self.env.cr.commit()
			# API CALL
			baselinker = self.env['baselinker']
			# max 1000 records per result
			current = 0
			page = 1
			page_size = 1000
			read = True
			messages = []
			last_status = 'UNKNOWN'
			products_created = products_updated = 0
			# odczyt listy z baselinker'a
			rmessages = []
			while read:
				response = baselinker.getInventoryProductsList( inventory_id=inventory.inventory_id, page=page)
				last_status = response.get('status') or 'UNKNOWN'
				if response and ('SUCCESS' in response.get('status')):
					results = response
					recno = 0
					rmessages.append(results['products'])
					for message in results['products']:
						current += 1
						messages.append(message)
						recno += 1
					if current >= (page * page_size): page += 1
					if recno == 0 or recno < page_size: 
						break
				else: 
					break
				if read != True:
					break

			if config.get('baselinker_set_debug'): 
				_logger.info("""\ndownload_inventory_products.getInventoryProductsList[%s]: messages = %s""" % ( inventory.inventory_id, len(messages) ))

			#inventory.number += len(messages)
			result = {}
			result['status'] = last_status
			result['products'] = messages
			rr = result and ('SUCCESS' in result.get('status'))
			# odtwarzanie/aktualizacja z listy wg limitu
			if result and ('SUCCESS' in result.get('status')):
				products = ''
				current = 0
				limit = 1000
				n = 0 #round(len(messages)/limit)
				for n in range( round(len(messages)/limit) ):
					product_list = messages[n*limit:n*limit+limit]
					result = baselinker.getInventoryProductsData( product_list, inventory_id=inventory.inventory_id)
					if 'SUCCESS' in result.get('status'):
						products = result.get('products')
						created, updated = self.updateProcuct( inventory, products)
						products_created += created
						products_updated += updated
						if config.get('baselinker_set_debug'): 
							_logger.info("""\n ProductsData[%s]: products %s,  created = %s, updated = %s""" % ( inventory.inventory_id, len(products), created, updated ))
					else:
						if config.get('baselinker_set_debug'): 
							_logger.info("""\ndownload_inventory_products.getInventoryProductsData[%s]: ERROR %s""" % ( inventory.inventory_id, result ))
						break

					if (products_created > baselinker_product_create_limit):
						status = 'create_required'
						break

				if (products_created > baselinker_product_create_limit):
					status = 'create_required'
					break

				if (n < round(len(messages)/limit)) or (( len(messages) < limit) and ( len(messages) > 0)):
					n = round(len(messages)/limit)
					product_list = messages[n*limit:n*limit+limit]
					result = baselinker.getInventoryProductsData( product_list, inventory_id=inventory.inventory_id)
					if 'SUCCESS' in result.get('status'):
						products = result.get('products')
						created, updated = self.updateProcuct( inventory, products)
						products_created += created
						products_updated += updated
						if config.get('baselinker_set_debug'): 
							_logger.info("""\n ProductsData[%s]: products %s,  created = %s, updated = %s""" % ( inventory.inventory_id, len(products), created, updated ))
					else:
						if config.get('baselinker_set_debug'): 
							_logger.info("""\ndownload_inventory_products.getInventoryProductsData[%s]: ERROR %s""" % ( inventory.inventory_id, result ))
						break
			if (products_created > baselinker_product_create_limit):
				if config.get('baselinker_set_debug'): 
					_logger.info("""\n3. download_inventory_products.LIMITS EXCEEDED: created[%s] updated[%s]""" % (products_created, products_updated) )
				status = 'create_required'
				break

		end_timer = time.time()
		result_timer = round( ((end_timer - start_timer)) / 1 )
		if config.get('baselinker_set_debug'): 
			_logger.info("""\n====>>> download_inventory_products.RESULT:TIMER: %s """ % result_timer)
		inventory.state = status

		return products_created, products_updated


	"""	The method allows you to retrieve a list of catalogs 
		available in the BaseLinker storage.
	"""
	def getInventories(self):
		return None


	"""	The method allows you to add the BaseLinker catalogs. 
		Adding a catalog with the same identifier again will 
		cause updates of the previously saved catalog.
	"""
	def addInventory(self, vals):
		#for val in vals:
		#	if self.env['ir.config_parameter'].sudo().get_param('baselinker_set_debug'): 
		#		_logger.info("""\nBaseLinkerInventories.addInventory:: %s """ % val)
		return None


	"""	The method allows you to delete a catalog from BaseLinker storage.
	"""
	def deleteInventory(self):
		return None


class BaseLinker(models.Model):
	_name = 'baselinker'
	_description = 'Base Linker'

	name = fields.Char()

	date = fields.Datetime()
	msg = fields.Binary()
	response_code = fields.Integer(store=True, default=0, )
	response_msg  = fields.Text(store=True, default="", )

	status = fields.Selection([
		('imported','Imported'),
		('ierror','Imported Error'),
		('unsent','Unsent'),
		('sent','Sent'),
		('error','Error')
		], string="Send status", readonly=True, index=True, default='unsent')

	state = fields.Selection([
		('draft', 'Project'),
		('waiting', 'Waiting'),
		('confirmed', 'Confirmed'),
		('assigned', 'Assigned'),
		('done', 'Done'),
		('cancel', 'Cancelled'),
		], string='Status', readonly=True, index=True, default='draft')


	
	"""
		(getInventories)
	"""
	@api.model
	def getInventories(self):
		result = response = None
		values = self.env['blapi']._prepare(model='baselinker')
		rest = self.env['blapi'].create(values)
		config = self.env['res.config.settings'].sudo().get_values()
		result = rest.Init(config)
		if result:
			inventories = rest.getInventories()
			if inventories:
				inventory = inventories.json()
				for record in inventory['inventories']:
					# aktualizacja słownika
					catalog = self.env['bl_inventories'].search([('inventory_id','=',record.get('inventory_id') )])
					if not catalog:
						vals = {
							'inventory_id': record.get('inventory_id'),
							'name': record.get('name'),
							'description': record.get('description'),
							'languages': record.get('languages'),
							'default_language': record.get('default_language'),
							'price_groups': record.get('price_groups'),
							'default_price_group': record.get('default_price_group'),
							'warehouses': record.get('warehouses'),
							'default_warehouse': record.get('default_warehouse'),
							'reservations': record.get('reservations'),
							'is_default': record.get('is_default'),
						}
						self.env['bl_inventories'].sudo().create(vals)
					else:
						vals = {}
						#self.env['bl_inventories'].sudo().update(vals)
					
					if record['is_default']:
						baselinker_inventory_id = record['inventory_id']
						config['baselinker_inventory_id'] = baselinker_inventory_id
						params = self.env['ir.config_parameter'].sudo()
						params.set_param('baselinker_inventory_id', baselinker_inventory_id)
						api = self.env['baselinker.config.settings'].sudo().search([('id','=',1)])
						api.sudo().update({'baselinker_inventory_id': baselinker_inventory_id})
						config = self.env['res.config.settings'].sudo().get_values()
						if config.get('baselinker_set_debug'): 
							_logger.info("""\nBASELINKER.getInventories CFG:: %s""" % (config))
		else:
			return False

	"""
		(getInventoryPriceGroups).baselinker_price_group_id
	"""
	@api.model
	def getInventoryPriceGroups(self):
		result = response = None
		values = self.env['blapi']._prepare(model='baselinker')
		rest = self.env['blapi'].create(values)
		config = self.env['res.config.settings'].sudo().get_values()
		result = rest.Init(config)
		if result:
			ret = rest.getInventoryPriceGroups()
			if ret:
				pricegroups = ret.json()
				for record in pricegroups['price_groups']:
					if record['is_default']:
						baselinker_price_group_id = record['price_group_id']
						config['baselinker_price_group_id'] = baselinker_price_group_id
						params = self.env['ir.config_parameter'].sudo()
						params.set_param('baselinker_price_group_id',baselinker_price_group_id)
						api = self.env['baselinker.config.settings'].sudo().search([('id','=',1)])
						api.sudo().update({'baselinker_price_group_id':baselinker_price_group_id})
						config = self.env['res.config.settings'].sudo().get_values()
						if config.get('baselinker_set_debug'): 
							_logger.info("""\nBASELINKER.getInventoryPriceGroups :: %s""" % (config ))
		else:
			return False

	"""
		(getInventoryProductsData).product_id
{
	"error_code": "ERROR_STORAGE_ID",
	"error_message": "Invalid catalogue identifier provided.",
	"status": "ERROR"
}
	"""
	@api.model
	def getInventoryProductsData(self, product_id, inventory_id=None):
		result = False
		response = None
		values = self.env['blapi']._prepare(model='baselinker')
		rest = self.env['blapi'].create(values)
		config = self.env['res.config.settings'].sudo().get_values()
		if not inventory_id:
			inventory_id = config['baselinker_inventory_id']
		result = rest.Init(config)
		if result:
			ret = rest.getInventoryProductsData( product_id, inventory_id)
			if ret:
				product = ret.json()
				result = product

		return result	

	@api.model
	def getExternalStorageProductsData(self, storage_id, product_id):
		result = False
		response = None
		values = self.env['blapi']._prepare(model='baselinker')
		rest = self.env['blapi'].create(values)
		config = self.env['res.config.settings'].sudo().get_values()
		result = rest.Init(config)
		if result:
			ret = rest.getExternalStorageProductsData( storage_id, product_id)
			if ret:
				product = ret.json()
				result = product

		return result	



	@api.model
	def getImageFrom_url(self, url):
		data = ""
		try:
			data = base64.b64encode(requests.get(url.strip()).content).replace(b"\n", b"")
		except Exception as e:
			logging.exception(e)
		return data

	"""
		(updateInventoryProductsStock)
	"""
	@api.model
	def updateInventoryProductsStock(self, inventory_id, product):
		result = False
		response = None
		values = self.env['blapi']._prepare(model='baselinker')
		rest = self.env['blapi'].create(values)
		config = self.env['res.config.settings'].sudo().get_values()
		if not inventory_id:
			inventory_id = config['baselinker_inventory_id']
		result = rest.Init(config)

		if result:
			ret = rest.updateInventoryProductsStock( inventory_id, product)
			if ret:
				result= ret.json()

		return result



	"""
		(getInventoryIntegrations)
	"""
	@api.model
	def getInventoryIntegrations(self, inventory_id=None):
		result = False
		response = None
		values = self.env['blapi']._prepare(model='baselinker')
		rest = self.env['blapi'].create(values)
		config = self.env['res.config.settings'].sudo().get_values()
		if not inventory_id:
			inventory_id = config['baselinker_inventory_id']
		result = rest.Init(config)

		if result:
			ret = rest.getInventoryIntegrations( inventory_id)
			if ret:
				result= ret.json()

		return result

	"""
		(getInventoryProductsStock)
	"""
	@api.model
	def getInventoryProductsStock(self, inventory_id=None, page=None):
		result = False
		response = None
		values = self.env['blapi']._prepare(model='baselinker')
		rest = self.env['blapi'].create(values)
		config = self.env['res.config.settings'].sudo().get_values()
		if not inventory_id:
			inventory_id = config['baselinker_inventory_id']
		result = rest.Init(config)

		if result:
			ret = rest.getInventoryProductsStock( inventory_id, page=page)
			if ret:
				result= ret.json()

		return result


	"""
		(getInventoryProductsList)
	"""
	@api.model
	def getInventoryProductsList(self, inventory_id=None, page=None):
		result = False
		response = None
		values = self.env['blapi']._prepare(model='baselinker')
		rest = self.env['blapi'].create(values)
		config = self.env['res.config.settings'].sudo().get_values()
		if not inventory_id:
			inventory_id = config['baselinker_inventory_id']
		result = rest.Init(config)

		if result:
			ret = rest.getInventoryProductsList( inventory_id, page=page)
			if ret:
				result= ret.json()

		return result


	"""
		(getInventoryWarehouses).baselinker_warehouse_id
	"""
	@api.model
	def getInventoryWarehouses(self):
		result = response = None
		values = self.env['blapi']._prepare(model='baselinker')
		rest = self.env['blapi'].create(values)
		config = self.env['res.config.settings'].sudo().get_values()
		result = rest.Init(config)
		if result:
			ret = rest.getInventoryWarehouses()
			if ret:
				warehouses = ret.json()
				for record in warehouses['warehouses']:
					# aktualizacja słownika
					warehouse = self.env['bl_inventorywarehouses'].search([('warehouse_id','=',record.get('warehouse_id') )])
					if not warehouse:
						vals = {
							'name': record.get('name'),
							'warehouse_type': record.get('warehouse_type'),
							'warehouse_id': record.get('warehouse_id'),
							'description': record.get('description'),
							'stock_edition': record.get('stock_edition'),
							'is_default': record.get('is_default'),
						}
						self.env['bl_inventorywarehouses'].sudo().create(vals)
					else:
						vals = {}
	
					if record['is_default']:
						baselinker_warehouse_id = record['warehouse_id']
						config['baselinker_warehouse_id'] = baselinker_warehouse_id
						params = self.env['ir.config_parameter'].sudo()
						params.set_param('baselinker_warehouse_id',baselinker_warehouse_id)
						api = self.env['baselinker.config.settings'].sudo().search([('id','=',1)])
						api.sudo().update({'baselinker_warehouse_id':baselinker_warehouse_id})
						config = self.env['res.config.settings'].sudo().get_values()
		else:
			return False

	"""
		(getOrderByID)
	"""
	@api.model
	def getOrderByID(self, order):
		result = response = None
		values = self.env['blapi']._prepare(model='baselinker')
		rest = self.env['blapi'].create(values)
		config = self.env['res.config.settings'].sudo().get_values()
		result = rest.Init(config)
		if result:
			return rest.getOrderByID( order)
		else:
			return False

	"""
		(getInvoices)
	"""
	@api.model
	def getInvoices(self, byDate=None, offset=None, limit=None, invoice_id=None, order_id=None):
		result = response = None
		values = self.env['blapi']._prepare(model='baselinker')
		rest = self.env['blapi'].create(values)
		config = self.env['res.config.settings'].sudo().get_values()
		result = rest.Init(config)
		if result:
			return rest.getInvoices(byDate=byDate, offset=offset, limit=limit)
		else:
			return False


	"""
		(getOrders)
	"""
	@api.model
	def getOrders(self, byDate=None, offset=None, limit=None):
		result = response = None
		values = self.env['blapi']._prepare(model='baselinker')
		rest = self.env['blapi'].create(values)
		config = self.env['res.config.settings'].sudo().get_values()
		result = rest.Init(config)
		if result:
			return rest.getOrders(byDate=byDate, offset=offset, limit=limit)
		else:
			return False

	"""
		(setOrderFields)
	"""
	@api.model
	def setOrderFields(self, order_id, admin_comments=None, parameters=None):
		result = response = None
		values = self.env['blapi']._prepare(model='baselinker')
		rest = self.env['blapi'].create(values)
		config = self.env['res.config.settings'].sudo().get_values()
		result = rest.Init(config)
		if result:
			return rest.setOrderFields( order_id, admin_comments=admin_comments, parameters=parameters)
		else:
			return False

	"""
		(setOrderProductFields)
	"""
	@api.model
	def setOrderProductFields(self, order_id, order_product_id=None, location=None):
		result = response = None
		values = self.env['blapi']._prepare(model='baselinker')
		rest = self.env['blapi'].create(values)
		config = self.env['res.config.settings'].sudo().get_values()
		result = rest.Init(config)
		if result:
			return rest.setOrderProductFields( order_id, order_product_id, location)
		else:
			return False


	"""
		(setOrderStatus)
	"""
	@api.model
	def setOrderStatus(self, order_id, status_id):
		result = response = None
		values = self.env['blapi']._prepare(model='baselinker')
		rest = self.env['blapi'].create(values)
		config = self.env['res.config.settings'].sudo().get_values()
		result = rest.Init(config)
		if result:
			return rest.setOrderStatus( order_id, status_id)
		else:
			return False

	"""
		(addOrder)
	"""
	def addOrder(self, jdata):
		result = response = None
		values = self.env['blapi']._prepare(model='baselinker')
		rest = self.env['blapi'].create(values)
		config = self.env['res.config.settings'].sudo().get_values()
		result = rest.Init(config)
		if result:
			return rest.addOrder(jdata)
		else:
			return False


	"""
		(addInventoryProduct)
	"""	
	def addInventoryProduct(self, jdata):
		result = response = None
		values = self.env['blapi']._prepare(model='baselinker')
		rest = self.env['blapi'].create(values)
		config = self.env['res.config.settings'].sudo().get_values()
		result = rest.Init(config)
		if result:
			return rest.addInventoryProduct(jdata)
		else:
			return False

	"""
		(addProduct)
	"""	
	def addProduct(self, jdata):
		result = response = None
		values = self.env['blapi']._prepare(model='baselinker')
		rest = self.env['blapi'].create(values)
		config = self.env['res.config.settings'].sudo().get_values()
		result = rest.Init(config)
		if result:
			return rest.addProduct(jdata)
		else:
			return False

	"""
		(addCategory)
	"""	
	def addCategory(self, jdata, mode='create'):
		result = response = None
		values = self.env['blapi']._prepare(model='baselinker')
		rest = self.env['blapi'].create(values)
		config = self.env['res.config.settings'].sudo().get_values()
		result = rest.Init(config)
		if result:
			return rest.addCategory(jdata, mode)
		else:
			return False

	"""
		(getOrderStatusList)
	"""	
	def getOrderStatusList(self):
		result = response = None
		values = self.env['blapi']._prepare(model='baselinker')
		rest = self.env['blapi'].create(values)
		config = self.env['res.config.settings'].sudo().get_values()
		result = rest.Init(config)
		if result:
			return rest.getOrderStatusList()
		else:
			return False

	"""
		(getOrderSourcesList)
	"""	
	def getOrderSourcesList(self):
		result = response = None
		values = self.env['blapi']._prepare(model='baselinker')
		rest = self.env['blapi'].create(values)
		config = self.env['res.config.settings'].sudo().get_values()
		result = rest.Init(config)
		if result:
			return rest.getOrderSourcesList()
		else:
			return False


class ProductCategory(models.Model):
	_inherit = 'product.category'
	#_inherits = ['mail.thread', 'mail.activity.mixin', 'image.mixin']

	bl_category_id = fields.Integer('BaseLink Category_ID', default=None)
	bl_status = fields.Selection([('draft','Unregistered'),('registered','Registered'),('err','Error'),('updated','Updated')], 'BaseLinker Status', default='draft', copy=False,)
	bl_storage_id = fields.Char('Storage ID')

	def message_post(self, body):
		return None

	def baselinker_register(self):
		for category in self:
			msg = "Update Failed"
			colour = "#7a0d0d"
			if not category.bl_category_id:
				category.bl_status = 'registered'
				json_category = {
					'storage_id': 'bl_1',
					'name': "%s" % category.name,
					'parent_id' : 0
				}
				baselinker = self.env['baselinker']
				result = baselinker.addCategory(json_category)
				if result:
					msg = json.dumps(result.json(), indent=4, sort_keys=True)
					result = result.json()
					if result:
						category.bl_category_id = result['category_id']
						category.bl_storage_id = result['storage_id']
						category.bl_status = 'registered'
						colour = "#113d09"
					else:
						category.bl_status = 'err'
				else:
					category.bl_status = 'err'
			msg = """<b style='color: %s;'>%s </b> """ % ( colour, msg.replace("\n","<br>"))
			category.message_post(body=msg)
		return None

	def baselinker_update(self):
		for category in self:
			msg = "Update Failed"
			colour = "#7a0d0d"
			if category.bl_category_id:
				category.bl_status = 'registered'
				json_category = {
					'category_id': category.bl_category_id,
					'storage_id': 'bl_1',
					'name': "%s" % category.name,
					'parent_id' : 0
				}
				baselinker = self.env['baselinker']
				result = baselinker.addCategory(json_category, mode='update')
				if result:
					msg = json.dumps(result.json(), indent=4, sort_keys=True)
					result = result.json()
					if result:
						category.bl_category_id = result['category_id']
						category.bl_storage_id = result['storage_id']
						category.bl_status = 'registered'
						colour = "#113d09"
					else:
						category.bl_status = 'err'
				else:
					category.bl_status = 'err'
				msg = """<b style='color: %s;'>%s </b> """ % ( colour, msg.replace("\n","<br>"))
				category.message_post(body=msg)
		return None


class ProductImage(models.Model):
	_name = 'product.image'
	_description = "Product Image"
	_inherit = ['image.mixin']
	_order = 'sequence, id'

	name = fields.Char("Name", required=True)
	sequence = fields.Integer(default=10, index=True)

	image_1920 = fields.Image(required=True)

	product_tmpl_id = fields.Many2one('product.template', "Product Template", index=True, ondelete='cascade')
	product_variant_id = fields.Many2one('product.product', "Product Variant", index=True, ondelete='cascade')
	video_url = fields.Char('Video URL',
							help='URL of a video for showcasing your product.')
	embed_code = fields.Html(compute="_compute_embed_code", sanitize=False)

	can_image_1024_be_zoomed = fields.Boolean("Can Image 1024 be zoomed", compute='_compute_can_image_1024_be_zoomed', store=True)

	@api.depends('image_1920', 'image_1024')
	def _compute_can_image_1024_be_zoomed(self):
		for image in self:
			image.can_image_1024_be_zoomed = image.image_1920 and tools.is_image_size_above(image.image_1920, image.image_1024)

	@api.depends('video_url')
	def _compute_embed_code(self):
		for image in self:
			image.embed_code = get_video_embed_code(image.video_url)

	@api.constrains('video_url')
	def _check_valid_video_url(self):
		for image in self:
			if image.video_url and not image.embed_code:
				raise ValidationError(_("Provided video URL for '%s' is not valid. Please enter a valid video URL.", image.name))

	@api.model_create_multi
	def create(self, vals_list):
		context_without_template = self.with_context({k: v for k, v in self.env.context.items() if k != 'default_product_tmpl_id'})
		normal_vals = []
		variant_vals_list = []

		for vals in vals_list:
			if vals.get('product_variant_id') and 'default_product_tmpl_id' in self.env.context:
				variant_vals_list.append(vals)
			else:
				normal_vals.append(vals)

		return super().create(normal_vals) + super(ProductImage, context_without_template).create(variant_vals_list)

class ProductTemplate(models.Model):
	_inherit = "product.template"

	product_template_image_ids = fields.One2many('product.image', 'product_tmpl_id', string="Extra Product Media", copy=True)

	def _get_images(self):
		"""Return a list of records implementing `image.mixin` to
		display on the carousel on the website for this template.

		This returns a list and not a recordset because the records might be
		from different models (template and image).

		It contains in this order: the main image of the template and the
		Template Extra Images.
		"""
		self.ensure_one()
		return [self] + list(self.product_template_image_ids)

"""	Extend model 
	InventoryProduct

	addInventoryProduct
	getInventoryProduct
"""
class ProductProduct(models.Model):
	_inherit = 'product.product'

	bl_name = fields.Char(translate=False, store=True, copy=False,)
	bl_price = fields.Float('BL Price', default=0, store=True, copy=False, )
	bl_storage_id = fields.Char('Storage ID', copy=False,)
	bl_inventory_id = fields.Char(string='Catalog ID', copy=False,)
	bl_inventory_catalog_id = fields.Many2one('bl_inventories', string='Catalog Name', copy=False,)
	bl_category_name = fields.Char(related='bl_inventory_catalog_id.name', string='Baselink Category', copy=False,)
	bl_product_id = fields.Char('Main product id', copy=False,)
	bl_variant_id = fields.Char('Variant product id', copy=False,)
	bl_parent_id = fields.Char('Product parent ID', copy=False,)
	bl_is_bundle = fields.Boolean('Is the given product a part of a bundle', copy=False,)
	#bl_ean = fields.Char(related='barcode', string='Product EAN number')
	bl_ean = fields.Char(string='Product EAN number', copy=False,)
	bl_sku = fields.Char(related="default_code", string='Product SKU number', copy=False,)
	bl_tax_rate = fields.Float('VAT tax rate', default=0, store=True, copy=False, )
	bl_weight = fields.Float( related='weight', store=True, string="BL Weight", copy=False, )
	bl_height = fields.Float('Product height', default=0, store=True, copy=False, )
	bl_width  = fields.Float('Product width', default=0, store=True, copy=False, )
	bl_length = fields.Float('Product length', default=0, store=True, copy=False, )
	bl_star = fields.Selection([
		('0', 'No starring'),
		('1','1 star'),
		('2','2 stars'),
		('3','3 stars'),
		('4','4 stars'),
		('5','5 stars')
		], 'Product star', default='0')

	bl_location = fields.Char('Product Location', copy=False,)
	bl_auction_id = fields.Char('Auction ID', copy=False,)
	bl_attributes = fields.Char('Attributes', copy=False,)
	bl_price_brutto = fields.Float('single item gross price', default=0, store=True, copy=False, )
	bl_quantity = fields.Integer('quantity of pieces', copy=False,)
	bl_category_id = fields.Integer( related='categ_id.bl_category_id', copy=False,)
	bl_prices_ids = fields.Many2many('product.pricelist',string='the price group ID', copy=False,)
	bl_text_fields_ids = fields.Many2many('bl_text_fields', copy=False,)
	bl_images_ids = fields.Many2many('bl_images', copy=False,)
	bl_links_ids = fields.Many2many('bl_links', string='Links to external warehouses', copy=False,)
	bl_status = fields.Selection([
		('draft','Unregistered'),
		('registered','Registered'),
		('err','Error'),
		('updated','Updated')
		], 'BaseLinker Status', default='draft')

	bl_attr_value = fields.Many2one('product.attribute.value', copy=False,)

	#@api.onchange('bl_inventory_id')
	def onchange_bl_inventory_id(self, inventory_id=None):
		for record in self:
			if inventory_id:
				domain = [('inventory_id','=', inventory_id)]
			else:
				domain = [('inventory_id','=', record.bl_inventory_id)]
			inventory =  self.env['bl_inventories'].sudo().search(domain)
			if inventory:
				record.bl_inventory_catalog_id = inventory.id

	def from_baselinker_images_update(self, baselinker_api, product_template, images_array):
		for image in images_array:
			url = images_array[image]
			can_write = True
			for iname in product_template.product_template_image_ids:
				if image == iname.name:
					can_write = False
			if can_write:
				data = baselinker_api.getImageFrom_url(url)
				if not product_template.image_1920:
					product_template.image_1920 = data
				product_template.sudo().write({ 'product_template_image_ids': [(0, 0, {'name': image, 'image_1920': data})] })

	"""
		Update from BaseLinker
	"""
	def get_inventory_products_update(self):
		start_timer = time.time()
		colour = "#113d09"
		context = dict(self._context or {})
		if self.bl_storage_id:
			storage = self.bl_storage_id.split('_')
		else:
			storage = "bl_"
		if storage[0] in ['shop', 'warehouse']:
			raise UserError("""Produkt składowany jest poza magazynem Baselinker. Aktualizacja danych nie jest możliwa.""" )
		bl_variant_id = context.get('bl_variant_id')
		bl_product_id = context.get('bl_product_id')
		variant = context.get('product_id')
		inventory_id = context.get('inventory_id')
		if bl_variant_id:
			product_id = bl_variant_id
		else:
			product_id = bl_product_id

		# BASELINKER API CALL
		baselinker = self.env['baselinker']
		result = baselinker.getInventoryProductsData( product_id, inventory_id=inventory_id)
		if result:
			if 'SUCCESS' in result.get('status'):
				record = self
				products = result.get('products')
				for product in products:
					data = json.dumps(result, indent=4, sort_keys=True)
					record.from_baselinker_images_update(baselinker, self.product_tmpl_id, products[product].get('images'))
					record.description = products[product].get('text_fields').get('description')

		else:
			bl_status = 'err'

		end_timer = time.time()
		result_timer = round( ((end_timer - start_timer) * 1000) / 60 )

		return True

	"""
		Find enad restore product by baselinker PRODUCT_ID
	"""
	def restore_inventory_product(self, product_id):
		result = None
		if not product_id or product_id in [0, '0']: return result
		inventories =  self.env['bl_inventories'].sudo().search([('in_use','!=', False )])
		baselinker = self.env['baselinker']
		found = False
		for inventory in inventories:
			result = baselinker.getInventoryProductsData( product_id, inventory_id=inventory.inventory_id)
			if 'SUCCESS' in result.get('status'):
				products = result.get('products')
				for product in products:
					if product in product_id:
						found = True
						data = self.get_inventory_products_data( inventory_id=inventory.inventory_id, product_id=product_id, create=True, is_variant=False)
						domain = domain = [('bl_variant_id','=', product_id ), ('active','=',True)]
						result = self.env['product.product'].sudo().search(domain)
						return result

		return result

	"""
		getExternalStorageProductsData( storage_id, product_id, variant_id )
	"""
	@api.model
	def getExternalStorageProductsData( self, storage_id, product_id, variant_id ):
		colour = "#113d09"
		context = dict(self._context or {})
		create = True
		created = False
		start_timer = time.time()
		storage = storage_id.split('_')
		if storage[0] in ['shop','warehouse']:
			if variant_id not in [False, None, '', '0', 0]:
				product = variant_id
				is_variant = True
			else:
				product = product_id
				is_variant = False
			domain = [('bl_variant_id','=',product_id),('bl_storage_id','=', storage_id)]
			# sprawdzamy lokalnie
			base_result = self.env['product.product'].sudo().search(domain)

			for result in base_result:
				return result

			# jeśli nie ma lokalnie, to odtwarzamy produkt
			# BASELINKER API CALL
			baselinker = self.env['baselinker']
			result = baselinker.getExternalStorageProductsData( storage_id, product_id)
			inventory_id = None
			if result:
				if 'SUCCESS' in result.get('status'):
					products = result.get('products')
					for product in products:
						# jeśli zestaw
						is_bundle = products[product].get('is_bundle')
						# wykluczenie istniejących
						break_for = False
						for stored_product in base_result:
							if product in stored_product.bl_product_id:
								#_logger.info("""\n====>> STORED %s""" % product )
								break_for = True
								continue
						if break_for:
							continue

						pname = products[product].get('name')
						if pname in [None, False, ""]:
							pname = "BASELINKER PRODUCT ID: %s" % product

						# przygotowanie danych dla tworzenia rekordu produktu
						if create: 
							list_price= products[product].get('price_brutto')
							product_values = {
								'name': pname,
								'list_price': list_price,
								'weight': products[product].get('weight'),
								'description': products[product].get('description'),
								'type': 'product',
								'standard_price': products[product].get('average_cost'),
								'detailed_type': 'product',
							}
						# jeśli są warianty
						variant_values = []
						if products[product].get('variants'):
							bl_attr = self.env['product.attribute'].sudo().search([('name','=','baselinker')])
							if not bl_attr:
								bl_attr = self.env['product.attribute'].sudo().create({'name': 'baselinker'})
							variants = products[product].get('variants')
							if variants: 
								value_ids = []
							else: 
								value_ids = None
							for variant in variants:
								bl_attr_value = self.env['product.attribute.value'].sudo().search([('name','=', "%s" % variant.get('variant_id')  )])
								if not bl_attr_value:
									bl_attr_value = self.env['product.attribute.value'].create({'name': "%s" % variant.get('variant_id'), 'attribute_id': bl_attr.id})
								list_price = variant.get('price')
								variant_values.append({
									'name': variant.get('name'),
									'bl_name': variant.get('name'),
									'default_code': variant.get('sku'),
									'bl_sku': variant.get('sku'),
									'bl_ean': variant.get('ean'),
									'bl_product_id': product,
									'bl_variant_id': "%s" % variant.get('variant_id'),
									'lst_price': list_price,
									'bl_price': list_price,
									'bl_attr_value': bl_attr_value.id,
									'bl_inventory_id': inventory_id,
									'bl_storage_id': storage_id,
									'description': products[product].get('description'),
									'weight': variant.get('weight'),
									'bl_height': variant.get('height'),
									'bl_width':  variant.get('width'),
									'bl_length': variant.get('length'),					
									'bl_is_bundle': is_bundle,
								})
								value_ids.append( bl_attr_value.id )
							# dodanie listy atrybutów dla tworzonych wariantów
							product_values['attribute_line_ids'] = [(0, 0, { 
								'attribute_id': bl_attr.id, 
								'value_ids': [(6, 0, value_ids)],
							}) ]

						if create:
							# UTWORZENIE REKORDU
							template = self.env['product.template'].sudo().create(product_values)
							created = True

							# aktualizacja fotografi
							images = products[product].get('images')
							# DEVELOPMENT ONLY
							#self.from_baselinker_images_update( baselinker, template, images)

							# aktualizacja danych wariantów
							for variant in template.product_variant_ids:
								if variant_values:
									for vval in variant_values:
										vs = vval.get('bl_variant_id')
										vv = variant.bl_variant_id
										#porównanie przez atrybut !!!!
										match = False
										for attr in variant.product_template_attribute_value_ids:
											if vs in attr.name:
												match = True
												break
										if match:
											value = {
												'bl_name': vval.get('name'),
												'bl_variant_id': vval.get('bl_variant_id'),
												'bl_product_id': product,
												'default_code': vval.get('default_code'),
												'bl_sku':  vval.get('bl_sku'),
												'bl_ean':  vval.get('bl_ean'),
												'bl_inventory_id': inventory_id,
												'bl_inventory_catalog_id': None,
												'bl_storage_id': storage_id,
												'bl_status': 'registered',
												'lst_price': vval.get('lst_price'),
												'bl_price': vval.get('bl_price'),
												'description': products[product].get('description'),
												'weight': vval.get('weight'),
												'bl_height': vval.get('height'),
												'bl_width':  vval.get('width'),
												'bl_length': vval.get('length'),					
												'bl_is_bundle': is_bundle,
											}
											res = variant.sudo().write(value)
											#variant.onchange_bl_inventory_id( inventory_id=inventory_id )
											msg = "%s" % vval
											msg = """<b style='color: %s;'>Utworzono: %s </b> """ % ( colour, msg.replace("\n","<br>"))
											variant.message_post(body=msg)
											#_logger.info("""\nVARIANT product for: vs(%s)attr %s = %s""" % (vs, attr, attr.name) )
											break
								else:
									if is_variant:
										variant.bl_product_id = orig_product_id
										variant.bl_variant_id = product_id
									else:
										variant.bl_product_id = product
										variant.bl_variant_id = product
									# WYŁĄCZENIE AKTUALIZACJI POLA KATEGORIA W PRODUKTACH Z ZEWNĘTRZNYCH MAGAZYNÓW <- WYMAGANA IMPLEMENTACJA ROZSZERZONA
									value = {
										#'bl_category_id': products[product].get('category_id'),
										'bl_name': "%s" % products[product].get('name'),
										'default_code': products[product].get('default_code'),
										'bl_sku': products[product].get('sku'),
										'bl_ean': products[product].get('ean'),
										'bl_inventory_id': inventory_id,
										'bl_status': 'registered',
										'bl_inventory_catalog_id': None,
										'bl_storage_id': storage_id,
										'description': products[product].get('description'),
										'weight': products[product].get('weight'),
										'bl_height': products[product].get('height'),
										'bl_width':  products[product].get('width'),
										'bl_length': products[product].get('length'),					
										'bl_is_bundle': is_bundle,
									}
									res = variant.sudo().write(value)
									#variant.onchange_bl_inventory_id( inventory_id=inventory_id )
									msg = "Registered from Baselinker" #json.dumps( products[product], indent=4, sort_keys=True)
									msg = """<b style='color: %s;'>%s </b> """ % ( colour, msg.replace("\n","<br>"))
									variant.message_post(body=msg)
						else:
							colour = "#22093d"
							template = self.env['product.template'].sudo().search(domain)
							# aktualizacja fotografi
							images = products[product].get('images')
							self.from_baselinker_images_update(template, images)
							msg = "Update from Baselinker"
							msg = """<b style='color: %s;'>%s </b> """ % ( colour, msg.replace("\n","<br>"))
							template.product_variant_ids[0].message_post(body=msg)

			else:
				bl_status = 'err'

			end_timer = time.time()
			result_timer = round( ((end_timer - start_timer) * 1000) / 60 )

		base_result = self.env['product.product'].sudo().search(domain)
		return base_result

	"""
		Create Product and Variants
		====>>> get_inventory_products_data: CREATE(False)  [4832] / 4832
	"""
	@api.model
	def get_inventory_products_data(self, params=None, inventory_id=None, product_id=None, create=False, is_variant=True):
		start_timer = time.time()
		colour = "#113d09"
		context = dict(self._context or {})
		if product_id in [None, False, '']: product_id = context.get('product_id')
		if not inventory_id: inventory_id = context.get('inventory_id')
		# z formularza
		bl_variant_id = context.get('bl_variant_id')
		bl_product_id = context.get('bl_product_id')
		if bl_variant_id or bl_product_id:
			if bl_variant_id:
				product_id = bl_variant_id
			else:
				product_id = bl_product_id
			create = False
			is_variant = False

		if is_variant:
			domain = [('bl_variant_id','=', product_id), ('active','=',True)]
		else:
			if isinstance( product_id, list):
				domain = [('bl_product_id','in', product_id ), ('active','=',True)]
			else:
				domain = [('bl_product_id','=', product_id ), ('active','=',True)]

		if product_id in [None, False, '']: return False

		# ustalenie listy produktów
		base_result = self.env['product.product'].sudo().search(domain)
		if  base_result and (len(base_result) == 1):
			orig_product_id = base_result.product_tmpl_id.id
		else:
			orig_product_id = None

		if create or (not base_result):
			create = True
			#base_result = self
			is_variant = False
			orig_product_id = None

		# ustalenie katalogu
		config = self.env['res.config.settings'].sudo().get_values()
		if not inventory_id:			
			inventory_id = config.get('baselinker_inventory_id')
		inventory =  self.env['bl_inventories'].sudo().search([('inventory_id','=', inventory_id )])

		# BASELINKER API CALL
		baselinker = self.env['baselinker']
		result = baselinker.getInventoryProductsData( product_id, inventory_id=inventory_id)
		colour = "#d61c1c"

		if result:
			if 'SUCCESS' in result.get('status'):
				products = result.get('products')
				for product in products:
					# przerwanie jeśli nie wolno odtwarzać zestawów
					is_bundle = products[product].get('is_bundle')
					if config.get('baselinker_skip_bundle') and is_bundle:
						continue
					# wykluczenie istniejących
					break_for = False
					for stored_product in base_result:
						if product in stored_product.bl_product_id:
							#_logger.info("""\n====>> STORED %s""" % product )
							break_for = True
							continue
					if break_for:
						continue

					pname = products[product].get('text_fields').get('name')
					if pname in [None, False, ""]:
						pname = "BASELINKER PRODUCT ID: %s" % product

					# przygotowanie danych dla tworzenia rekordu produktu
					if create: 
						prices = products[product].get('prices')
						list_price = 0
						for price in prices: list_price = prices[price]
						product_values = {
							'name': pname,
							'list_price': list_price,
							'weight': products[product].get('weight'),
							'description': products[product].get('text_fields').get('description'),
							'standard_price': products[product].get('average_cost'),
							'type': 'product',
						}
					# jeśli są warianty
					variant_values = []
					if products[product].get('variants'):
						bl_attr = self.env['product.attribute'].sudo().search([('name','=','baselinker')])
						if not bl_attr:
							bl_attr = self.env['product.attribute'].sudo().create({'name': 'baselinker'})
						variants = products[product].get('variants')
						if variants: 
							value_ids = []
						else: 
							value_ids = None
						for variant in variants:
							bl_attr_value = self.env['product.attribute.value'].sudo().search([('name','=',variant)])
							if not bl_attr_value:
								bl_attr_value = self.env['product.attribute.value'].create({'name': variant, 'attribute_id': bl_attr.id})
							prices = variants[variant].get('prices')
							for price in prices: list_price = prices[price]							
							variant_values.append({
								'name': variants[variant].get('name') or variant,
								'bl_name': variants[variant].get('name'),
								'default_code': variants[variant].get('sku'),
								'bl_sku': variants[variant].get('sku'),
								'bl_ean': variants[variant].get('ean'),
								'bl_product_id': product,
								'bl_variant_id': variant,
								'lst_price': list_price,
								'bl_price': list_price,
								'bl_attr_value': bl_attr_value.id,
								'bl_inventory_id': inventory_id,
								'description': products[product].get('text_fields').get('description'),
								'weight': products[product].get('weight'),
								'bl_height': products[product].get('height'),
								'bl_width':  products[product].get('width'),
								'bl_length': products[product].get('length'),
								'bl_is_bundle': is_bundle,
							})
							value_ids.append( bl_attr_value.id )
						# dodanie listy atrybutów dla tworzonych wariantów
						product_values['attribute_line_ids'] = [(0, 0, { 
							'attribute_id': bl_attr.id, 
							'value_ids': [(6, 0, value_ids)],
						}) ]

					if create:
						route_ids = inventory.route_ids.ids
						if route_ids:
							product_values['route_ids'] = [(6, 0, route_ids)]
						if inventory.supplier_ids:
							supplier_ids = inventory.supplier_ids
						else:
							supplier_ids = None
						# UTWORZENIE REKORDU
						template = self.env['product.template'].sudo().create(product_values)
						if supplier_ids:
							for supplier in supplier_ids:
								company_id = self.company_id
								if not company_id:
									company_id = self.env.company
								if company_id.currency_id:
									currency_id = company_id.currency_id.id
								values = {
									'name': supplier.id,
									'product_code': pname,
									'product_tmpl_id': template.id,
									'min_qty': 0,
									'price': 0,
									'currency_id': company_id.currency_id.id,
									'delay': 1,
									'company_id': company_id.id
								}
								self.env['product.supplierinfo'].sudo().create(values)

						template.product_variant_ids[0].bl_inventory_catalog_id = inventory.id

						# aktualizacja fotografi
						images = products[product].get('images')
						# aktualizacja danych wariantów
						for variant in template.product_variant_ids:
							if variant_values:
								for vval in variant_values:
									vs = vval.get('bl_variant_id')
									vv = variant.bl_variant_id
									match = False
									for attr in variant.product_template_attribute_value_ids:
										if vs in attr.name:
											match = True
											break
									if match:
										value = {
											'bl_name': vval.get('name'),
											'bl_variant_id': vval.get('bl_variant_id'),
											'bl_product_id': product,
											'default_code': vval.get('default_code'),
											'bl_sku':  vval.get('bl_sku'),
											'bl_ean':  vval.get('bl_ean'),
											'bl_inventory_id': inventory_id,
											'bl_inventory_catalog_id': inventory.id,
											'bl_status': 'registered',
											'lst_price': vval.get('lst_price'),
											'bl_price': vval.get('bl_price'),
											'weight': vval.get('weight'),
											'bl_height': vval.get('height'),
											'bl_width':  vval.get('width'),
											'bl_length': vval.get('length'),
											'description': products[product].get('text_fields').get('description'),
											'bl_is_bundle': is_bundle,
										}
										res = variant.sudo().write(value)
										msg = "%s" % vval
										msg = """<b style='color: %s;'>Utworzono: %s </b> """ % ( colour, msg.replace("\n","<br>"))
										variant.message_post(body=msg)
										break
							else:
								if is_variant:
									variant.bl_product_id = orig_product_id
									variant.bl_variant_id = product_id
								else:
									variant.bl_product_id = product
									variant.bl_variant_id = product
								value = {
									'bl_category_id': products[product].get('category_id'),
									'bl_name': "%s" % products[product].get('text_fields').get('name'),
									'default_code': products[product].get('default_code'),
									'bl_sku': products[product].get('sku'),
									'bl_ean': products[product].get('ean'),
									'bl_inventory_id': inventory_id,
									'bl_status': 'registered',
									'bl_inventory_catalog_id': inventory.id,
									'weight': products[product].get('weight'),
									'bl_height': products[product].get('height'),
									'bl_width':  products[product].get('width'),
									'bl_length': products[product].get('length'),
									'description': products[product].get('text_fields').get('description'),
									'bl_is_bundle': is_bundle,
								}
								res = variant.sudo().write(value)
								msg = "Registered from Baselinker" #json.dumps( products[product], indent=4, sort_keys=True)
								msg = """<b style='color: %s;'>%s </b> """ % ( colour, msg.replace("\n","<br>"))
								variant.message_post(body=msg)
						msg = json.dumps( products[product], indent=4, sort_keys=True)
						msg = """<b style='color: %s;'><pre>%s</pre> </b> """ % ( colour, msg.replace("\n","<br>"))
						template.message_post(body=msg)
					else:
						colour = "#22093d"
						template = self.env['product.template'].sudo().search(domain)
						# aktualizacja fotografi
						images = products[product].get('images')
						self.from_baselinker_images_update(template, images)
						msg = "Update from Baselinker"
						msg = """<b style='color: %s;'>%s </b> """ % ( colour, msg.replace("\n","<br>"))
						template.product_variant_ids[0].message_post(body=msg)

		else:
			bl_status = 'err'

		end_timer = time.time()
		result_timer = round( ((end_timer - start_timer) * 1000) / 60 )

		return True

	"""
	"""
	@api.model
	def getInventoryProductsList(self, inventory_id=None):
		config = self.env['res.config.settings'].sudo().get_values()
		if config.get('baselinker_set_debug'): _logger.info("""\n====>>> getInventoryProductsList: SKIP PROCESS""" )
		return None
		
		start_timer = time.time()
		context = dict(self._context or {})
		if not inventory_id:
			inventory_id = config.get('baselinker_inventory_id')
			domain = [('in_use','=', True)]
		else:
			domain = [('inventory_id','=', inventory_id)]
			
		counter = 0
		inventories = self.env['bl_inventories'].sudo().search(domain)
		inventory_number = 0
		for inventory in inventories:
			query = """SELECT max(bl_product_id) FROM product_product WHERE bl_inventory_id = '%s'""" % inventory.inventory_id
			self.env.cr.execute(query)
			rows = self.env.cr.dictfetchall()
			if rows and rows[0].get('max'):
				last_bl_product = rows[0].get('max')
			else:
				last_bl_product = "empty"
			# API CALL
			baselinker = self.env['baselinker']
			# max 1000 records per result
			current = 0
			page = 1
			page_size = 1000
			read = True
			messages = []
			last_status = 'UNKNOWN'
			while read:
				response = baselinker.getInventoryProductsList( inventory_id=inventory.inventory_id, page=page)
				last_status = response.get('status') or 'UNKNOWN'
				if response and ('SUCCESS' in response.get('status')):
					results = response
					recno = 0
					for message in results['products']:
						if last_bl_product in message:
							if config.get('baselinker_set_debug'): 
								_logger.info("""\n====>>> getInventoryProductsList[%s]: RESULT MESSAGE %s / """ %  ( inventory.inventory_id, last_bl_product ))
							read = False
							break
						current += 1
						messages.append(message)
						recno += 1
					if current >= (page * page_size): page += 1
					if recno == 0 or recno < page_size: 
						break
				else: 
					break
				if read != True:
					if config.get('baselinker_set_debug'): 
						_logger.info("""\n====>>> getInventoryProductsList[%s]: RESULT BREAK %s""" % ( inventory.inventory_id, read ))
					break

			inventory.number += len(messages)

			result = {}
			result['status'] = last_status
			result['products'] = messages
			rr = result and ('SUCCESS' in result.get('status'))
			if result and ('SUCCESS' in result.get('status')):
				current = 0
				limit = 1000
				n = 0 
				for n in range( round(len(messages)/limit) ):
					product_list = messages[n*limit:n*limit+limit]
					data = self.get_inventory_products_data( inventory_id=inventory.inventory_id, product_id=product_list, create=True, is_variant=False)
				
				if (n < round(len(messages)/limit)) or (( len(messages) < limit) and ( len(messages) > 0)):				
					n = round(len(messages)/limit)
					product_list = messages[n*limit:n*limit+limit]
					data = self.get_inventory_products_data( inventory_id=inventory.inventory_id, product_id=product_list, create=True, is_variant=False)

			inventory_number += inventory.number

		end_timer = time.time()
		result_timer = round( ((end_timer - start_timer) * 1000) / 60 )
		if config.get('baselinker_set_debug'): _logger.info("""\n====>>> getInventoryProductsList.RESULT:TIMER: %s """ % result_timer)
		return inventory_number


	def baselinker_update(self):
		return None
		config = self.env['res.config.settings'].sudo().get_values()
		inventory_id = config.get('baselinker_inventory_id')
		for product in self:
			# prepare
			config = self.env['res.config.settings'].sudo().get_values()
			json_product = {
				'product_id': product.bl_product_id,
				"category_id": product.bl_category_id,
				"inventory_id": product.bl_inventory_id or inventory_id,
				"is_bundle": False,
				"ean": product.bl_ean,
				"sku": product.bl_sku,
				"tax_rate": 23,
				"weight": product.weight,
				"height": product.bl_height,
				"width":  product.bl_width,
				"length": product.bl_length,
				"star": product.bl_star,
				"text_fields": {
					"name": product.name,
					"description": product.description or "",
					"description_extra1": product.description_sale or "",
					"description_extra2": product.description_purchase or "",
					"description_extra3": product.description_pickingout or "",
					"description_extra4": product.description_pickingin or ""
				}
			}
			product.bl_inventory_id = product.bl_inventory_id or inventory_id
			# volume price & taxes
			if product.list_price:
				if product.taxes_id:
					tax_rate = round(product.taxes_id.amount)
				else:
					tax_rate = 0
				price_brutto = product.list_price
				if tax_rate > 0:
					price_wholesale_netto = round( product.list_price / tax_rate, 2)
				else:
					price_wholesale_netto = price_brutto

				# price
				price_key = "%s" % config['baselinker_price_group_id']
				price_value = price_brutto
				json_product['prices'] = { price_key: price_value }
				json_product['tax_rate'] = tax_rate

			# stock (warehouse_id)
			if product.virtual_available or product.virtual_available == 0:
				warehouse_key = "bl_%s" % config['baselinker_warehouse_id']
				qty = product.virtual_available
				json_product['stock'] = { warehouse_key: qty}

			# image
			if product.image_1920:
				json_product['images'] = []
				image = product.image_1920.decode('utf-8')
				json_product['images'] = []
				json_product['images'].append( "data:%s" % image )

			# API CALL
			baselinker = self.env['baselinker']
			result = baselinker.addInventoryProduct(json_product)

			# save result
			msg = "Update Failed"
			colour = "#7a0d0d"
			if result:
				msg = json.dumps(result.json(), indent=4, sort_keys=True)
				result = result.json()
				if 'status' in result and result['status'] == "SUCCESS":
					product.bl_product_id = result['product_id']
					product.bl_storage_id = result.get('storage_id') or 'bl_1'
					product.bl_status = 'updated'
					colour = "#113d09"
				else:
					product.bl_status = 'err'
					
			else:
				#product.bl_status = 'err'
				msg = "Komunikacja zakończona niepowodzeniem"

			msg = """<b style='color: %s;'>%s </b> """ % ( colour, msg.replace("\n","<br>"))
			product.message_post(body=msg)

	
	def baselinker_register(self):
		return None
		for product in self:
			msg = "Update Failed"
			colour = "#7a0d0d"
			json_product = {
				'weight': product.weight,
			}
			config = self.env['res.config.settings'].sudo().get_values()
			inventory_id = config.get('baselinker_inventory_id')
			json_product = {
				'weight': product.weight,
				'product_id': product.bl_product_id,
				"category_id": product.bl_category_id,
				"inventory_id": product.bl_inventory_id or inventory_id,
				"is_bundle": False,
				"ean": product.bl_ean,
				"sku": product.bl_sku,
				"tax_rate": 23,
				"weight": product.weight,
				"height": product.bl_height,
				"width":  product.bl_width,
				"length": product.bl_length,
				"star": product.bl_star,
				"text_fields": {
					"name": product.name,
					"description": product.description or "",
					"description_extra1": product.description_sale or "",
					"description_extra2": product.description_purchase or "",
					"description_extra3": product.description_pickingout or "",
					"description_extra4": product.description_pickingin or ""
				}
			}
			if product.name:
				json_product['name'] = product.name
				json_product['description'] = product.description or "-"
				product.bl_inventory_id = product.bl_inventory_id or inventory_id
			if product.bl_storage_id:
				json_product['storage_id'] = product.bl_storage_id
			else:
				json_product['storage_id'] = "bl_1"

			if product.bl_category_id:
				json_product['category_id'] = product.bl_category_id
			else:
				json_product['category_id'] = "859873"

			if product.bl_ean not in [ 0, False, None, '0']:
				json_product['ean'] = product.bl_ean
			if product.bl_sku not in [ 0, False, None, '0']:
				json_product['sku'] = product.bl_sku
			if product.image_1920:
				image = product.image_1920.decode('utf-8')
				json_product['images'] = []
				json_product['images'].append( "data:%s" % image )
			if product.list_price:
				if product.taxes_id:
					tax_rate = round(product.taxes_id.amount)
				else:
					tax_rate = 0
				price_brutto = product.list_price
				if tax_rate > 0:
					price_wholesale_netto = round( product.list_price / tax_rate, 2)
				else:
					price_wholesale_netto = price_brutto
				json_product['price_brutto'] = price_brutto
				json_product['price_wholesale_netto'] = price_wholesale_netto
				json_product['tax_rate'] = tax_rate
			if product.virtual_available:
				json_product['quantity'] = product.virtual_available

			json_product['price_brutto'] = product.list_price			

			baselinker = self.env['baselinker']
			#####result = baselinker.addProduct(json_product)
			result = baselinker.addInventoryProduct(json_product)

			no_error = False
			if result:
				msg = json.dumps(result.json(), indent=4, sort_keys=True)
				result = result.json()
				#product.message_post(body=msg)
				if 'status' in result and result['status'] == "SUCCESS":
					product.bl_product_id = result.get('product_id')
					product.bl_storage_id = result.get('storage_id')
					product.bl_status = 'registered'
					colour = "#113d09"
					no_error = True
				else:
					product.bl_status = 'err'
			else:
				product.bl_status = 'err'

			msg = """<b style='color: %s;'>%s </b> """ % ( colour, msg.replace("\n","<br>"))
			product.message_post(body=msg)
			if no_error:
				product.baselinker_update()

	
	def update_baselinker_stock(self):
		records = self.env['product.product'].sudo().search([('active','=',True)])
		for record in records:
			if record.bl_status in ['updated']:
				record.baselinker_update()

"""
	(getInventoryIntegrations)
"""
class BaseLinkerInventoryIntegrations(models.Model):
	_name = 'baselinker.inventory.integrations'
	_description = 'BaseLinker Inventory Integrations'

	bl_name = fields.Char('Source identifier')
	name = fields.Char(related='bl_name',string='Source name')
	company_id = fields.Many2one('res.company', string='Company', default=lambda self: self.env.company)

	langs = fields.Char(string="Languages", help='An array of two-letter codes for the languages supported by a given integration')
	langs_ids = fields.Many2many('res.lang', string="Array of Languages", )
	accounts = fields.Char(help='List of connected accounts of a given integration, where the key is the account identifier and the value is the account name')

	"""
		(getInventoryIntegrations)
	"""
	def getInventoryIntegrations(self):
		baselinker = self.env['baselinker']
		result = baselinker.getInventoryIntegrations()
		if result:
			response = result #.json()
			if response['status'] == 'SUCCESS':
				integrations = response.get('integrations')
				for channel in integrations:
					record = integrations[channel]
					langs = record.get('langs')
					accounts = record.get('accounts')
					exist = self.env['baselinker.inventory.integrations'].search([('bl_name','=', channel)])
					if not exist:
						values = {
							'bl_name': channel,
							'langs': langs,
							'accounts': accounts
						}
						lang = False
						if langs:
							value_ids = []
							for code in langs:
								lang = self.env['res.lang'].sudo().search([('iso_code','=', code )])
								if lang:	
									value_ids.append( lang.id)

						if lang:
							values['langs_ids'] = [( 6, 0, value_ids)]
						record = self.env['baselinker.inventory.integrations'].sudo().create(values)
						

"""
	(getOrderSources)
"""
class BaseLinkerOrderSources(models.Model):
	_name = 'baselinker.order.sources'
	_description = 'BaseLinker Order Sources'

	bl_id = fields.Integer('Source identifier')
	name = fields.Char('Source name')
	source = fields.Char('Source')
	company_id = fields.Many2one('res.company', string='Company', default=lambda self: self.env.company)
	#company_id = fields.Many2one('res.company', string='Company', store=True, )
	#picking_type_id = fields.Many2one('stock.picking.type', store=True, )

	"""
		(getOrderSourcesList)
	"""
	@api.model
	def getOrderSourcesList(self):
		baselinker = self.env['baselinker']
		result = baselinker.getOrderSourcesList()
		if result:
			msg = json.dumps(result.json(), indent=4, sort_keys=True)
			response = result.json()
			if response['status'] == 'SUCCESS':
				for source in response['sources']:
					channel = response['sources'][source]
					for bl_id in channel:
						bstatus = self.env['baselinker.order.sources'].search([('bl_id','=', bl_id)])
						if not bstatus:
							values = {
								'bl_id': bl_id,
								'name': source,
								'source': channel[bl_id],
							}
							model = self.env['baselinker.order.sources']
							model.sudo().create(values)


"""	Extend model 
	BaseLinker order manager

	addOrder
	getOrders
	(...)
"""
class SaleOrder(models.Model):
	_inherit = 'sale.order'

	#self.company_id = self.env.company

	name = fields.Char(string='Order Reference', required=True, copy=False, readonly=True, index=True, default=lambda self: _('New'))

	baselinker_order = fields.Text('Order Source', copy=False, )

	bl_order_id = fields.Integer('Order identifier', copy=False,)
	bl_status = fields.Selection([
		('draft','Unregistered'),
		('registered','Registered'),
		('err','Error'),
		('updated','Updated'),
		('import','Do Importu'),
		('ready','Do Realizacji'),
		('waiting','Czeka na dostawę'),
		('cancel','Anulowane')
		], 'BaseLinker Status', default='draft')

	bl_status_id = fields.Many2one('baselinker.status', copy=False,)
	bl_shop_order_id = fields.Integer('Order ID given by the store', copy=False,)
	bl_external_order_id = fields.Char('An order identifier taken from an external source', copy=False,)
	bl_order_source = fields.Char('Order source', copy=False,)
	bl_order_source_id = fields.Integer('Source ID', copy=False,)
	bl_order_source_info = fields.Text('Description', copy=False,)
	bl_order_status_id = fields.Integer('status identifier', copy=False,)

	bl_date_add = fields.Integer('Date of order creation', copy=False,)
	bl_date_confirmed = fields.Integer('Order confirmation date if confirmed', copy=False,)
	bl_date_in_status = fields.Integer('Date from which the order is in current status', copy=False,)

	bl_delivery_method = fields.Char('Delivery method name', copy=False,)
	bl_delivery_price = fields.Float('Gross delivery price', copy=False,)
	bl_delivery_package_module = fields.Char('Courier name', copy=False,)
	bl_delivery_package_nr = fields.Char('Shipping number', copy=False,)
	bl_delivery_fullname = fields.Char('DA name and surname', copy=False,)
	bl_delivery_company = fields.Char('DA company', copy=False,)
	bl_delivery_address = fields.Char('DA street and number', copy=False,)
	bl_delivery_postcode = fields.Char('DA postcode', copy=False,)
	bl_delivery_city = fields.Char('DA city', copy=False,)
	bl_delivery_state = fields.Char('DA state/province', copy=False,)
	bl_delivery_country = fields.Char('DA country', copy=False,)
	bl_delivery_country_code = fields.Char('DA country code', copy=False,)
	bl_delivery_point_id = fields.Char('Pick-up point delivery ID', copy=False,)
	bl_delivery_point_name = fields.Char('Pick-up point delivery name', copy=False,)
	bl_delivery_point_address = fields.Char('Pick-up point delivery address', copy=False,)
	bl_delivery_point_postcode = fields.Char('Pick-up point delivery postcode', copy=False,)
	bl_delivery_point_city = fields.Char('Pick-up point delivery city', copy=False,)

	bl_invoice_fullname = fields.Char('BD name and surname', copy=False,)
	bl_invoice_company = fields.Char('BD company', copy=False,)
	bl_invoice_nip = fields.Char('BD Vat Reg. no./tax number', copy=False,)
	bl_invoice_address = fields.Char('BD street and house number', copy=False,)
	bl_invoice_postcode = fields.Char('BD postcode', copy=False,)
	bl_invoice_city = fields.Char('BD city', copy=False,)
	bl_invoice_state = fields.Char('BD state/province', copy=False,)
	bl_invoice_country = fields.Char('BD country', copy=False,)
	bl_invoice_country_code = fields.Char('BD country code', copy=False,)
	bl_want_invoice = fields.Boolean('Wants an invoice', copy=False,)
	bl_order_url = fields.Char('Order Mgmt', copy=False,)

	website_id = fields.Many2one('website', copy=False,)

	"""
		Extend Base Model
	"""
	def action_draft(self):
		action = super(SaleOrder, self).action_draft()
		for order in self:
			if order.state == 'draft':
				if order.bl_status:
					order.bl_status = 'import'
					order.set_order_status()
		return action

	def action_cancel(self):
		action = super(SaleOrder, self).action_cancel()
		for order in self:
			if order.state == 'cancel':
				if order.bl_status:
					order.bl_status = 'cancel'
					order.set_order_status()
		return action

	"""
		Manual Action:
		Button Set Status in BaseLinker
	"""
	def button_set_order_status(self):
		for order in self:
			if order.bl_order_id and int(order.bl_order_id) != 0:
				order_id = order.bl_order_id
				status = order.bl_status_id.bl_id
				baselinker = self.env['baselinker']
				result = baselinker.setOrderStatus( order_id, status )
				msg = json.dumps(result.json(), indent=4, sort_keys=True)
				colour = "#22093d"
				msg = """<b style='color: %s;'>BL Status:  %s </b> """ % ( colour, msg)
				order.message_post(body=msg)

		return None



	"""
		Manual Action:
		Button Set Return Status in BaseLinker
	"""
	def button_set_order_return_status(self):
		for order in self:
			if order.bl_order_id and int(order.bl_order_id) != 0:
				order_id = order.bl_order_id
				status = order.bl_status_id.bl_id
				baselinker = self.env['baselinker']
				result = baselinker.setOrderStatus( order_id, 301445 )
				msg = json.dumps(result.json(), indent=4, sort_keys=True)
				colour = "#22093d"
				msg = """<b style='color: %s;'>BL Status:  %s </b> """ % ( colour, msg)
				order.message_post(body=msg)

		return None

	"""
		Automatic Action:
		Set Status in BaseLinker
	"""
	def set_order_status(self):
		pass
		# for order in self:
		# 	stat = self.env['baselinker.status'].search([('status','=', order.bl_status)])
		# 	if stat:
		# 		order.bl_status_id = stat.id
		# 	order_id = order.bl_order_id
		# 	status = order.bl_status_id.bl_id
		# 	baselinker = self.env['baselinker']
		# 	result = baselinker.setOrderStatus( order_id, status )
		# 	msg = json.dumps(result.json(), indent=4, sort_keys=True)
		# 	colour = "#22093d"
		# 	msg = """<b style='color: %s;'>BL Status:  %s </b> """ % ( colour, msg)
		# 	order.message_post(body=msg)
		#
		# return None

	"""
		(getOrders)
	"""
	def _order_found(self, order_id):
		return self.env['sale.order'].search([('bl_order_id', '=', order_id)]) or None

	def bl_synchronization_state(self, state):
		query = """UPDATE """
		query += """ir_config_parameter SET value = '%s' WHERE key = 'bl_order_sync_state' """ % (state)
		self.env.cr.execute(query)
		self.env.cr.commit()
		self.env['ir.config_parameter'].sudo().set_param('bl_order_sync_state',state)



	def manual_lock_synchronization(self):
		if self.env['ir.config_parameter'].sudo().get_param('bl_order_sync_state') in ['busy','error']:
			msg = """\nODOO::\nTrwa obsługa priotytetowa zamówień.\nŻądanie aktualizacji zostaje przerwane.\n\nPonów żądanie gdy będzie to możliwe.""" 
			raise UserError(msg)
		query = """SELECT * FROM ir_config_parameter WHERE key = 'bl_order_sync_state';"""
		self.env.cr.execute(query)
		self.env.cr.commit()
		rows = self.env.cr.dictfetchall()
		if rows:
			for row in rows:
				if row.get('value') in ['busy','error']:
					msg = """\nSQL::\nTrwa obsługa priotytetowa zamówień.\nŻądanie aktualizacji zostaje przerwane.\n\nPonów żądanie gdy będzie to możliwe."""
					raise UserError(msg)
		self.bl_synchronization_state('busy')

	def lock_synchronization(self):
		result = False
		if self.env['ir.config_parameter'].sudo().get_param('bl_order_sync_state') in ['busy','error']:
			msg = """\nODOO::\nTrwa obsługa priotytetowa zamówień.\nŻądanie aktualizacji zostaje przerwane.\n\nPonów żądanie gdy będzie to możliwe."""
			raise UserError(msg)
		try:
			query = """SELECT * FROM ir_config_parameter WHERE key = 'bl_order_sync_state';"""
			self.env.cr.execute(query)
			self.env.cr.commit()
			rows = self.env.cr.dictfetchall()
			if rows:
				for row in rows:
					if row.get('value') in ['busy','error']:
						msg = """\nSQL::\nTrwa obsługa priotytetowa zamówień.\nŻądanie aktualizacji zostaje przerwane.\n\nPonów żądanie gdy będzie to możliwe."""
						raise UserError(msg)
			result = True
		except Exception as e:
			msg = """\nEnvSQL ERROR::\n\n%s""" % e
			raise UserError(msg)

		self.bl_synchronization_state('busy')
		return result


	def bl_getOrders(self):
		if not self.lock_synchronization(): return
		self.company_id = self.env.company
		config = self.env['res.config.settings'].sudo().get_values()
		if config.get('baselinker_set_debug'): _logger.info("""\nBaseLinkerGetOrders.getOrders ::start...""" )
		colour = "#22093d"
		config = self.env['res.config.settings'].sudo().get_values()
		website = config.get('baselinker_website_id')
		website_id = website.id or 1
		currentSecond = datetime.now().second
		currentMinute = datetime.now().minute
		currentHour   = datetime.now().hour
		currentDay  = datetime.now().day
		currentMonth  = datetime.now().month
		currentYear   = datetime.now().year
		if config.get('baselinker_query_date') == 'y':
			date_from = datetime( currentYear, 1, 1, 0, 0).strftime('%s')
		elif config.get('baselinker_query_date') == 'l120':
			date_from = (datetime.now() - timedelta(days=120)).strftime('%s')
		elif config.get('baselinker_query_date') == 'l30':
			date_from = (datetime.now() - timedelta(days=30)).strftime('%s')
		elif config.get('baselinker_query_date') == 'm':
			date_from = datetime( currentYear, currentMonth, 1, 0, 0).strftime('%s')
		elif config.get('baselinker_query_date') == 'w':
			date_from = (datetime.now() - timedelta(days=7)).strftime('%s')
		elif config.get('baselinker_query_date') == 'd':
			#date_from = datetime( currentYear, currentMonth, currentDay, 0, 0).strftime('%s')
			date_from = (datetime.now() - timedelta(days=1)).strftime('%s')
		elif config.get('baselinker_query_date') == 'h':
			date_from = (datetime.now() - timedelta(hours=1)).strftime('%s')
		else:
			date_from = datetime( currentYear, currentMonth, currentDay, currentHour, 0).strftime('%s')

		byDate = int(date_from)

		baselinker = self.env['baselinker']
		#result = baselinker.getOrders(byDate)
		# NEW w/Date seq.
		updated = created = current = 0
		messages = []
		response = {}
		read = True
		page_size = 100
		start_date = date_from
		last_date = None
		while read:
			recno = 0
			result = None
			result = baselinker.getOrders( start_date )
			response = result.json()
			if response and ('SUCCESS' in response.get('status')):
				results = response
				for message in results['orders']:
					messages.append(message)
					recno += 1
					current += 1
					start_date = message.get('date_confirmed')
	
			if recno < 100:
				read = False
		
		if config.get('baselinker_set_debug'): _logger.info("""\n\nORDERS IN RESPONSE = %s """ % (len(messages)))
		#return None		
		if result:
			#msg = json.dumps(result.json(), indent=4, sort_keys=True)
			response = result.json()
			response['orders'] = messages
			if response['status'] == 'SUCCESS':
				for order in response['orders']:
					#time.sleep(5) ##<- debug only
					SaleOrder = self._order_found( order.get('order_id'))
					if SaleOrder:
						# UPDATE SALE ORDER
						#
						updated += 1
						if SaleOrder.bl_order_status_id != order.get('order_status_id'):
							bl_status_id = self.env['baselinker.status'].search([('bl_id','=', order.get('order_status_id'))])
							updated_values = {}
							if bl_status_id:
								if config.get('baselinker_set_debug'): 
									_logger.info("""\nUPDATE SALE ORDER [%s]== %s ->  %s """ % ( order.get('order_id'), SaleOrder.bl_status_id.name, bl_status_id.name  ) )
								if order.get('order_status_id'):
									updated_values['bl_order_status_id'] = order.get('order_status_id')
								updated_values['bl_status_id'] = bl_status_id.id
								updated_values['bl_status'] = 'updated'
								SaleOrder.sudo().write(updated_values)
						
					else:
						# CREATE new SALE ORDER 
						#
						error = False
						SaleOrder = self.env['sale.order'].with_context(tracking_disable=False)
						# CREATE USER
						email = order.get('email') 
						if email in [False, None, ""]:
							email = 'Unknown_baselinker_user_email from order id %s [ %s ]' % ( order.get('order_id'), order.get('user_login') )
						ename = order.get('user_login')
						if order.get('user_login') in [False, None, ""]:
							ename = 'Unknown_baselinker_user_name from order id %s ' % ( order.get('order_id'))
						domain = [('email','=',email),('website_id','=',website_id)]
						if self.env['ir.config_parameter'].sudo().get_param('baselinker_set_debug'): _logger.info("""\nCREATE CONTACT:: PHONE [%s] """ % (order.get('phone')))
						customer = self.env['res.users'].sudo().search(domain)
						if not customer:
							values = {
								'email': email,
								'login': email,
								'name': ename,
								'website_id': website_id,
								'sel_groups_1_9_10': 9
							}

							if config.get('baselinker_client_invitation'):
								values['notification_type'] = 'email'
							else:
								values['notification_type'] = 'inbox'
							# ToDo: mail 
							customer = self.env['res.users'].sudo().create( values)

						customer.partner_id.phone = order.get('phone')

						# CREATE INVOICE CONTACT
						name = "%s" % order.get('invoice_fullname')
						if order.get('invoice_company'):
							name = "%s %s" % ( order.get('invoice_company'), order.get('invoice_fullname'))
						if len(name) > 0:
							country = self.env['res.country'].search([('code','=', order.get('invoice_country_code')  )])
							if country: 
								country_id = country.id
							else: 
								country_id = None
							#ToDo: dynamiczne wyszukiwanie na podstawie odebranych danych
							#found = customer.partner_id.child_ids.filtered(lambda p: ((p.name in name) and (p.type == 'invoice') ) )
							found = False
							for p in customer.partner_id.child_ids:
								if order.get('phone'): 
									p.phone = order.get('phone')
									if config.get('baselinker_set_debug'): _logger.info("""\nCREATE CONTACT:: PHONE [%s] %s """ % (order.get('phone'), p))
								if (p.name in name) and (p.type == 'invoice'):
									pp="%s%s%s%s%s" % (p.street or "", p.city or "", p.zip or "", p.country_id.id or "", p.vat or "")
									op="%s%s%s%s%s" % ( order.get('invoice_address') or "",
														order.get('invoice_city') or "",
														order.get('invoice_postcode') or "",
														country_id or "", 
														order.get('invoice_nip') or "" )
									if pp == op:
										found = True
									#_logger.info("""\n\npp = [%s]\nop = [%s] """ % (pp, op))
							if not found:
								vaules = {
									'name': name,
									'street': order.get('invoice_address'),
									'city': order.get('invoice_city'),
									'zip': order.get('invoice_postcode'),
									'country_id': country_id,
									'type': 'invoice',
								}
								customer.partner_id.sudo().write({'child_ids': [(0, 0, vaules)]})

						# CREATE DELIVERY CONTACT
						name = "%s" % order.get('delivery_fullname')
						if order.get('delivery_company'):
							name = "%s %s" % ( order.get('delivery_company'), order.get('delivery_fullname'))
						if len(name) > 0:
							country = self.env['res.country'].search([('code','=', order.get('delivery_country_code')  )])
							if country: 
								country_id = country.id
							else: 
								country_id = None
							found = False
							for p in customer.partner_id.child_ids:
								if (p.name in name) and (p.type == 'delivery'):
									pp="%s%s%s%s" % (p.street or "", p.city or "", p.zip or "", p.country_id.id or "")
									op="%s%s%s%s" % (order.get('delivery_address') or "",
													order.get('delivery_city') or "",
													order.get('delivery_postcode') or "",
													country_id or "")
									if pp == op:
										found = True
									#_logger.info("""\n\npp = [%s]\nop = [%s] """ % (pp, op))
							if not found:
								vaules = {
									'name': name,
									'street': order.get('delivery_address'),
									'city': order.get('delivery_city'),
									'zip': order.get('delivery_postcode'),
									'country_id': country_id,
									'type': 'delivery',
								}
								customer.partner_id.sudo().write({'child_ids': [(0, 0, vaules)]})

						currency = self.env['res.currency'].search([('name','=', order.get('currency') )])
						pricelist_ids = self.env['product.pricelist'].search([('currency_id','=', currency.id)])
						pricelist = None
						for pricelist_ in pricelist_ids:
							pricelist = pricelist_

						# PREPARE ORDER DATA
						values = {
							'company_id': customer.company_id.id,
							'website_id': website_id,
							#'picking_policy': picking_policy,
							'partner_id': customer.partner_id.id,
							'partner_invoice_id': customer.partner_id.id,
							'partner_shipping_id': customer.partner_id.id,
						}
						if pricelist:
							values['pricelist_id'] = pricelist.id

						if order.get('order_id'): values['bl_order_id'] = order.get('order_id')
						if order.get('status_id'): values['bl_status_id'] = order.get('status_id')
						if order.get('shop_order_id'): values['bl_shop_order_id'] = order.get('shop_order_id')
						if order.get('external_order_id'): values['bl_external_order_id'] = order.get('external_order_id')
						if order.get('order_source'): values['bl_order_source'] = order.get('order_source')
						if order.get('order_source_id'): values['bl_order_source_id'] = order.get('order_source_id')
						if order.get('order_source_info'): values['bl_order_source_info'] = order.get('order_source_info')
						if order.get('order_page'): values['client_order_ref'] = order.get('order_page')
						# DELIVERY
						if order.get('delivery_method'): values['bl_delivery_method'] = order.get('delivery_method')
						if order.get('delivery_price'): values['bl_delivery_price'] = order.get('delivery_price')
						if order.get('delivery_package_module'): values['bl_delivery_package_module'] = order.get('delivery_package_module')
						if order.get('delivery_package_nr'): values['bl_delivery_package_nr'] = order.get('delivery_package_nr')
						if order.get('delivery_fullname'): values['bl_delivery_fullname'] = order.get('delivery_fullname')
						if order.get('delivery_company'): values['bl_delivery_company'] = order.get('delivery_company')
						if order.get('delivery_address'): values['bl_delivery_address'] = order.get('delivery_address')
						if order.get('delivery_postcode'): values['bl_delivery_postcode'] = order.get('delivery_postcode')
						if order.get('delivery_city'): values['bl_delivery_city'] = order.get('delivery_city')
						if order.get('delivery_state'): values['bl_delivery_state'] = order.get('delivery_state')
						if order.get('delivery_country'): values['bl_delivery_country'] = order.get('delivery_country')
						if order.get('delivery_country_code'): values['bl_delivery_country_code'] = order.get('delivery_country_code')
						if order.get('delivery_point_id'): values['bl_delivery_point_id'] = order.get('delivery_point_id')
						if order.get('delivery_point_name'): values['bl_delivery_point_name'] = order.get('delivery_point_name')
						if order.get('delivery_point_address'): values['bl_delivery_point_address'] = order.get('delivery_point_address')
						if order.get('delivery_point_postcode'): values['bl_delivery_point_postcode'] = order.get('delivery_point_postcode')
						if order.get('delivery_point_city'): values['bl_delivery_point_city'] = order.get('delivery_point_city')
						# INVOICE
						if order.get('invoice_fullname'): values['bl_invoice_fullname'] = order.get('invoice_fullname')
						if order.get('invoice_company'): values['bl_invoice_company'] = order.get('invoice_company')
						if order.get('invoice_nip'): values['bl_invoice_nip'] = order.get('invoice_nip')
						if order.get('invoice_address'): values['bl_invoice_address'] = order.get('invoice_address')
						if order.get('invoice_postcode'): values['bl_invoice_postcode'] = order.get('invoice_postcode')
						if order.get('invoice_city'): values['bl_invoice_city'] = order.get('invoice_city')
						if order.get('invoice_state'): values['bl_invoice_state'] = order.get('invoice_state')
						if order.get('invoice_country'): values['bl_invoice_country'] = order.get('invoice_country')
						if order.get('invoice_country_code'): values['bl_invoice_country_code'] = order.get('invoice_country_code')
						if order.get('want_invoice'): values['bl_want_invoice'] = order.get('want_invoice')
						
						if order.get('order_status_id'): 
							values['bl_order_status_id'] = order.get('order_status_id')
							bl_status_id = self.env['baselinker.status'].search([('bl_id','=', order.get('order_status_id'))])
							if bl_status_id:
								values['bl_status_id'] = bl_status_id.id
						
						values['bl_status'] = 'registered'
						values['name'] = order.get('order_id')
						values['bl_order_url'] = "https://panel-e.baselinker.com/orders.php#order:%s" % order.get('order_id')
						# CREATE
						try:
							sale_order = SaleOrder.sudo().create( values)
							# prepare order.line
							error = False
							tax_id = None
							products = order.get('products')
							if not products:
								if config.get('baselinker_set_debug'):
									_logger.info("""\n\n- - - CREATE sale.order PRODUCT --> %s --- %s """ % ( order.get('products'), order))
								# set empty and skip create order line
								products = []
							for product in products:
								if product.get('storage') in ['db','bl']:
									if product.get('variant_id') not in [False, None, '', 0, '0']:
										product_id = product.get('variant_id')
									else:
										product_id = product.get('product_id')
									domain = [('bl_variant_id','=', product_id)]
									# SEARCH
									odoo_product = self.env['product.product'].search( domain, limit=1)
									# RESTORE
									if not odoo_product and product.get('storage') in ['db']:
										if config.get('baselinker_products_restore'):
											# Odtwarzanie produktu podczas odtwarzania zamówienia jeśli tak wskazuje konfiguracja
											odoo_product = self.env['product.product'].restore_inventory_product( product_id )
										if isinstance(odoo_product, dict):
											colour = "#d61c1c"
											msg = """<b style='color: %s;'>NOT FOUND PRODUCT:  %s </b> """ % ( colour, product)
											error = True
											sale_order.bl_status = 'err'
											sale_order.message_post(body=msg)
											# BREAK
											continue

										if odoo_product:
											ok = True
										else:
											colour = "#d61c1c"
											msg = """<b style='color: %s;'>NOT FOUND PRODUCT:  %s </b> """ % ( colour, product)
											error = True
											sale_order.bl_status = 'err'
											sale_order.message_post(body=msg)
											# BREAK
											continue

								else:
									# odtwarzanie produktu niebędącego w bazie Baselinker'a
									if config.get('baselinker_set_debug'):
										_logger.info("""\n\n- ERROR:5:: ORDER[%s] w/EXTERNAL PRODUCT !!! -> %s """ % ( order.get('order_id'), product ))
									storage_id = "%s_%s" % ( product.get('storage'), product.get('storage_id'))
									product_id = product.get('product_id')
									variant_id = product.get('variant_id')
									if config.get('baselinker_products_restore'):
										odoo_product = self.env['product.product'].getExternalStorageProductsData( storage_id, product_id, variant_id )
									else:
										colour = "#d61c1c"
										msg = """<b style='color: %s;'>NOT FOUND PRODUCT:  %s </b> """ % ( colour, product)
										error = True
										sale_order.bl_status = 'err'
										sale_order.message_post(body=msg)
										# BREAK
										continue

								taxes = self.env['account.tax'].search([('in_baselinker','=', True),('amount','=', product.get('tax_rate'))])
								tax_id = False
								if taxes:
									tax_id = [(6, 0, [taxes.id] )]
								else:
									taxes = self.env['account.tax'].search([('amount','=', product.get('tax_rate'))], limit=1)
									if taxes:
										tax_id = [(6, 0, [taxes.id] )]

								if odoo_product:
									values = {
											'company_id': customer.company_id.id,
											'name': odoo_product.name,
											'product_id': odoo_product.id,
											'product_uom_qty': product.get('quantity'),
											'product_uom': odoo_product.uom_id.id,
											'price_unit': product.get('price_brutto'),
											'order_id': sale_order.id,
											'customer_lead': 0.00,
											'tax_id': tax_id,
									}
									for route in odoo_product.bl_inventory_catalog_id.route_ids:
										values['route_id'] = route.id
										break

									sale_order.sudo().write( {'order_line': [(0, 0, values)]} )

							# ustalamy metodę dostawy jako produkt i dodajemy do wiersza zamówienia
							if sale_order.bl_delivery_price and sale_order.bl_delivery_price != 0:
								odoo_product = config.get('baselinker_delivery_product_id')
								if odoo_product:
									values = {
										'company_id': customer.company_id.id,
										'name': odoo_product.name,
										'product_id': odoo_product.id,
										'product_uom_qty': 1,
										'product_uom': odoo_product.uom_id.id,
										'price_unit': sale_order.bl_delivery_price,
										'order_id': sale_order.id,
										'customer_lead': 0.00,
										'tax_id': tax_id,
									}
									if tax_id:
										values['tax_id'] = tax_id
									sale_order.sudo().write( {'order_line': [(0, 0, values)]} )
								else:
									colour = "#22093d"

							if sale_order.company_id and sale_order.partner_id and sale_order.partner_shipping_id.id:
								sale_order.fiscal_position_id = self.env['account.fiscal.position'].with_company(
									sale_order.company_id).get_fiscal_position(sale_order.partner_id.id, sale_order.partner_shipping_id.id)
								sale_order.order_line._compute_tax_id()

							if error:
								sale_order.bl_status = 'err'
								colour = "#22093d"
								sale_order.set_order_status()
							else:
								try:
									error = True
									sale_order.action_confirm()
									error = False
								except:
									_logger.info("""\n==> 125. # BŁĄD :: sale_order.action_confirm()""")
									sale_order.bl_status = 'err'
									colour = "#22093d"
									sale_order.set_order_status()
									pass

							# STATUS FLOW
							# ------------------------
							# sprawdzam czy jest WZ
							if not error:
								pickings = self.env['stock.picking'].search([('sale_id','=', sale_order.id )])
								sale_order.bl_status = 'ready'
								if pickings:
									for picking in pickings:
										if picking.state not in ['done']:
											sale_order.bl_status = 'waiting'
								sale_order.set_order_status()
							error = False

							#
							msg = """<b style='color: %s;'>CREATE SALE ORDER: <pre> %s </pre></b> """ % ( colour, json.dumps( order, indent=4, sort_keys=True) )
							sale_order.message_post(body=msg)
							created += 1
						except Exception as Error:
							if config.get('baselinker_set_debug'): _logger.info("""\n==> Error (%s)""" % (Error))
							sale_order.bl_status = 'err'
							sale_order.set_order_status()
						# PRODUCT STOCK UPDATE ?
						##for order_line in sale_order.order_line:
						#if sale_order: if config.get('baselinker_set_debug'): _logger.info("""\n==> CREATE SALE ORDER: %s [%s]""" % (sale_order.name, sale_order.id))
						

		self.env['ir.config_parameter'].sudo().set_param('bl_order_sync_state','idle')
		if config.get('baselinker_set_debug'): _logger.info("""\n==> READ SALE ORDER: read (%s) update (%s) and created (%s)""" % ( current, updated, created))
		return None


	def bl_addOrder(self):
		for order in self:
			# nie rejestruj zamówień z niewłaściwym statusem
			if order.state in ['draft','cancel','error']:
				continue

			# przygotowanie danych rekordu do wysłania
			msg = "Register Failed"
			colour = "#7a0d0d"
			json_order = {
				'order_status_id': 121486, #order.bl_status_id.bl_id,
				'date_add': 1668767094,
				'user_login': '',
				'phone': '',
				'email': '',
				'user_comments': '',
				'admin_comments': '',
				'currency': 'PLN',
				'payment_method': '',
				'payment_method_cod': '0',
				'delivery_method': '',
				'delivery_price': 0,
				'delivery_fullname': '',
				'delivery_company': '',
				'delivery_address': '',
				'delivery_city': '',
				'delivery_postcode': '',
				'delivery_country_code': '',
				'delivery_point_id': '',
				'delivery_point_name': '',
				'delivery_point_address': '',
				'delivery_point_postcode': '',
				'delivery_point_city': '',
				'invoice_fullname': '',
				'invoice_company': '',
				'invoice_nip': '',
				'invoice_address': '',
				'invoice_city': '',
				'invoice_postcode': '',
				'invoice_country_code': '',
				'want_invoice': '0',
				'extra_field_1': 'ZAMÓWIENIE %s' % order.name,
				'extra_field_2': '',
				'delivery_country_code': '',
				'invoice_country_code': '',
			}

			# wysyłka danych
			baselinker = self.env['baselinker']
			result = baselinker.addOrder(json_order)
			no_error = False
			if result:
				msg = json.dumps(result.json(), indent=4, sort_keys=True)
				result = result.json()
				if 'status' in result and result['status'] == "SUCCESS":
					order.bl_order_id = result['order_id']
					order.bl_status = 'registered'
					colour = "#149400"
					no_error = True
				else:
					order.bl_status = 'err'
			else:
				order.bl_status = 'err'
			msg = """<b style='color: %s;'>%s </b> """ % ( colour, msg.replace("\n","<br>"))
			order.message_post(body=msg)

		return None


"""
	SELECT * FROM account_tax WHERE amount = 23 AND type_tax_use = 'sale'
"""
class AccountTax(models.Model):
	_inherit = 'account.tax'

	in_baselinker = fields.Boolean(string='Used in Baselinker', store=True, default=False, )

# EoF
class IrAttachment(models.Model):
	_inherit = 'ir.attachment'
	shipping_label = fields.Boolean(string = 'Shipping label exist',default = False)