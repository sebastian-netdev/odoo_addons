from odoo import fields, models, api

class DeliveryCarrier(models.Model):
    _inherit = 'delivery.carrier'

    short_name = fields.Char(string='Short name',default=None,size=10)




