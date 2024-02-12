from odoo import models, tools


class ResPartner(models.Model):
    _inherit = 'res.partner'

    @tools.ormcache('self.id')
    def x_get_tin_country(self):
        self.ensure_one()
        if self.country_id in self.env.ref('base.europe').country_ids and self.vat:
            country_code = self.vat[:2]
            if country_code.isalpha():
                return country_code
