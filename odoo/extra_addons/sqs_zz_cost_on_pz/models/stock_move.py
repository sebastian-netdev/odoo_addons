from odoo import fields, models, api
import logging

_logger = logging.getLogger(__name__)


class StockMove(models.Model):
    _inherit = 'stock.move'

    zz_price = fields.Float(related="product_id.standard_price")