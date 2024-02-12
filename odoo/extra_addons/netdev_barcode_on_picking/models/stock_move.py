from odoo import fields, models, api
import logging

_logger = logging.getLogger(__name__)

#
# class StockMove(models.Model):
#     _inherit = 'stock.move.line'
#
#     barcode = fields.Char(related="product_id.barcode")