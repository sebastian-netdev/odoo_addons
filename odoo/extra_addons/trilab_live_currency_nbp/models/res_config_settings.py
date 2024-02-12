import logging
from datetime import datetime, timedelta

import requests

from odoo import fields, models
from odoo.addons.currency_rate_live.models.res_config_settings import COUNTRY_CURRENCY_PROVIDERS

_logger = logging.getLogger(__name__)


# monkey patching currency_rate_live
COUNTRY_CURRENCY_PROVIDERS.update({'PL': 'nbp'})


class ResCompany(models.Model):
    _inherit = 'res.company'

    currency_provider = fields.Selection(selection_add=[('nbp', 'NBP (Poland)')], ondelete={'nbp': 'cascade'})

    def _parse_nbp_data(self, available_currencies):
        """
        This method is used to update the currencies by using NBP (National Polish Bank) service API.
        Rates are given against PLN
        """

        # this is url to fetch active (at the moment of fetch) average currency exchange table
        request_url = 'https://api.nbp.pl/api/exchangerates/tables/{}/?format=json'
        available_currency_codes = available_currencies.mapped('name')

        result = {}

        try:
            # there are 3 tables with currencies:
            #   A - most used ones average,
            #   B - exotic currencies average,
            #   C - common bid/sell
            # we will parse first one and if there are unmatched currencies, proceed with second one

            for table_type in ['A', 'B']:

                if not available_currency_codes:
                    break

                response = requests.get(request_url.format(table_type))
                response_data = response.json()

                for exchange_table in response_data:
                    # there *should not be* be a more than one table in response, but let's be on a safe side
                    # and parse this in a loop as response is a list

                    # effective date of this table
                    table_date = datetime.strptime(exchange_table['effectiveDate'], '%Y-%m-%d').date() + timedelta(
                        days=1
                    )

                    # add base currency
                    if 'PLN' not in result:
                        result['PLN'] = (1.0, table_date, exchange_table['no'])

                    for rec in exchange_table['rates']:
                        if rec['code'] in available_currency_codes:
                            result[rec['code']] = (1.0 / rec['mid'], table_date, exchange_table['no'])
                            available_currency_codes.remove(rec['code'])

        except (requests.RequestException, ValueError):
            # connection error, the request wasn't successful or date was not parsed
            return False

        return result

    def _generate_currency_rates(self, parsed_data):
        super()._generate_currency_rates({k: v[:2] for k, v in parsed_data.items()})

        for company in self:
            for currency, (rate, date_rate, *extra) in parsed_data.items():
                if extra:
                    self.env['res.currency.rate'].search(
                        [('currency_id.name', '=', currency), ('name', '=', date_rate), ('company_id', '=', company.id)]
                    ).write({'x_nbp_table': extra[0]})
