# -*- coding: utf-8 -*-
# noinspection PyStatementEffect
{
    'name': "Poland - Accounting (PeSport)",

    'description': """
        Polish chart of accounts and taxes.
    """,

    'author': "Trilab/SQS CoA for PeSport",
    'website': "https://soniqsoft.pl",

    'category': 'Accounting/Localizations/Account Charts',
    'version': '1.6',

    'depends': [
        'trilab_jpk_vat',
        'trilab_pl_reports',
    ],

    'data': [
        'data/l10n_pl_trilab_chart_data.xml',
        'data/trilab_tax_group_data.xml',
        'data/account.account.template.csv',
        'data/l10n_pl_trilab_chart_data_def.xml',
        'data/account.group.template.csv',
        'data/res.country.state.csv',
        'data/trilab_tax_data.xml',
        'data/account_chart_template_data.xml',
        'data/account_tax_repartition_line_template.xml',
    ],

    'images': [
        'static/description/banner.png'
    ],

    'installable': True,
    'auto_install': False,
    'application': True,
    'license': 'OPL-1'
}
