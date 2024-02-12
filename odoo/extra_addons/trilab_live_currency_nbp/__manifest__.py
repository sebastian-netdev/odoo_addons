# noinspection PyStatementEffect
{
    'name': 'Trilab Live Currency Exchange Rate for NBP (Poland)',
    'author': 'Trilab',
    'website': "https://trilab.pl",
    'support': 'odoo@trilab.pl',
    'version': '1.5',
    'category': 'Accounting',
    'summary': """
        Import exchange rates from the Internet. NBP (Polish National Bank)""",
    'description': """
        Module extends built-in live currency module to use NBP (National Polish Bank) REST API to
        download current exchange rates.

        It downloads data from table A (average exchange rates for popular currencies) then (if needed)
        from table B (average exchange rates for other currencies).

        It uses PLN (zł) as a base currency.

        Module fetches rate table that is active at the moment of download.

    """,
    'depends': [
        'currency_rate_live'
    ],
    'data': [
        'views/res_currency.xml'
    ],
    'demo': [
    ],
    'images': [
        'static/description/banner.png'
    ],
    'installable': True,
    'auto_install': True,
    'license': 'OPL-1',
}
