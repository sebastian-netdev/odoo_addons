# -*- coding: utf-8 -*-

{
    'name': 'Auto confirm WZ',
    'version': '1.0',
    'description': '''
        Autoconfirming WZ if products in WZ is available in warehouse
    ''',
    'summary': '',
    'author': 'Soniqsoft',
    'depends': ['base', 'stock', 'sale'],
    'data': [
        'views/stock_picking_tree_inherit.xml',
    ],
    'installable': True,
    'auto_install': False,
    'application' : False
}