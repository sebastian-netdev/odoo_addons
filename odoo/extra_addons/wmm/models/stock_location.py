from odoo import fields, models, api



class StockLocation(models.Model):
    _inherit = 'stock.location'

    location_priority = fields.Integer(string='Location priority',default=0)
    is_trolley = fields.Boolean(string='Is a trolley?',default=False)