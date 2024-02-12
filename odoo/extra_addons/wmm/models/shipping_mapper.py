from odoo import fields, models, api




class ShippingMapper(models.Model):
    _name = 'shipping.mapper'


    name = fields.Char(string='Shipping method name')
    shipping = fields.Many2one('delivery.carrier',string='Shipping method',default=None)

