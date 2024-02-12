# -*- coding: utf-8 -*-

{
    'name': 'NetDEV barcode on pickings',
    'version': '1.0',
    'description': '''
        Add barcode on every picking
    ''',
    'summary': '',
    'author': 'NetDEV',
    'depends': ['base', 'purchase', 'stock'],
    'data': [
        'views/stock_picking_inherit.xml',
    ],
    'installable': True,
    'auto_install': False,
    'application' : False
}