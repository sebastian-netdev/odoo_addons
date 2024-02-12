from odoo import models, fields


class Partner(models.Model):
    _inherit = 'res.partner'

    x_pl_vat_tp = fields.Boolean(
        string='TP',
        default=False,
        help='Istniejące powiązania między nabywcą a dokonującym dostawy towarów lub usługodawcą, o których mowa '
             'w art. 32 ust. 2 pkt 1 ustawy.',
    )
