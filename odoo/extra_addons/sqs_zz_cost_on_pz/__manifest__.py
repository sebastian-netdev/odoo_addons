# -*- coding: utf-8 -*-

{
    'name': 'ZZ cost on PZ',
    'version': '1.0',
    'description': '''
        Add cost of every product from PZ to ZZ
    ''',
    'summary': '',
    'author': 'Soniqsoft',
    'depends': ['base', 'purchase', 'stock'],
    'data': [
        'views/stock_picking_inherit.xml',
    ],
    'installable': True,
    'auto_install': False,
    'application' : False
}