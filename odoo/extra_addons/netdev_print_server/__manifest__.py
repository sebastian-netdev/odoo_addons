{
    "name": "Print server",
    'description': """
    Module to using linux cups for labels printer
    """,
    "author": "NetDEV Sebastian Romanczukiewicz",
    "website":"https://netdev.site",
    "depends":['sale'],
    "version":"1.0",
    "data":[
        'views/printers.xml',
        'views/settings.xml',
        'views/server.xml',
        'security/ir.model.access.csv'],
    "category":"Inventory",
    "installable":True,
    "application":True,
}