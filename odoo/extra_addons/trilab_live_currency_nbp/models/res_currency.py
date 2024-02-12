from datetime import timedelta
from odoo import api, fields, models, _


class Currency(models.Model):
    _inherit = 'res.currency'

    x_nbp_table = fields.Char(string='NBP Table No', compute='_compute_current_rate')

    @api.depends('rate_ids.rate')
    def _compute_current_rate(self):
        super()._compute_current_rate()
        rate_date = self._context.get('date') or fields.Date.context_today(self)
        company = self._context.get('company_id') or self.env.company.id

        for currency_id in self:
            if currency_id.active:
                rate_id = (
                    self.env['res.currency.rate']
                    .sudo()
                    .search(
                        [
                            ('currency_id', '=', currency_id.id),
                            ('rate', '!=', False),
                            ('company_id', '=', company),
                            ('name', '<=', rate_date),
                        ],
                        limit=1,
                    )
                )

                currency_id.x_nbp_table = rate_id.x_nbp_table

            else:
                currency_id.x_nbp_table = False


class ResCurrencyRate(models.Model):
    _inherit = 'res.currency.rate'

    x_nbp_table = fields.Char(string='NBP Table No')

    @api.depends_context('x_show_table_info')
    def name_get(self):
        if self.env.context.get('x_show_table_info'):
            return [
                (
                    curr_rate.id,
                    _(
                        'NBP exchange rate table no. %(table)s of %(date)s, exchange rate = %(rate)s',
                        table=curr_rate.x_nbp_table,
                        date=curr_rate.name - timedelta(days=1),
                        rate=curr_rate.inverse_company_rate,
                    ),
                )
                for curr_rate in self
            ]
        return super(ResCurrencyRate, self).name_get()
