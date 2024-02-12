{
    "name": "Warehouse Management Module",
    'description': """
    Module for picking and packing module,
    """,
    "author": "NetDEV Sebastian Romanczukiewicz",
    "website": "https://netdev.site",
    "depends": ['stock', 'netdev_print_server', 'hfb_baselinker_api', 'stock_picking_batch'],
    "version": "1.0",
    "data": [
        'views/settings.xml',
        'views/shipping_mapper.xml',
        'views/wmm_main.xml',
        'views/stock_location.xml',
        'views/stock_picking.xml',
        'views/stock_picking_kanban.xml',
        'views/delivery_carrier.xml',
        'views/cron.xml',
        'security/ir.model.access.csv'],
    "category": "Inventory",
    "installable": True,
    "application": True,
}
