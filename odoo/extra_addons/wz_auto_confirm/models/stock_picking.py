from odoo import fields, models, api
import logging

_logger = logging.getLogger(__name__)

class StockPicking(models.Model):
    _inherit = 'stock.picking'

    completionPercent = fields.Integer(string="Completion %", compute="_compute_completion_percent")

    def _compute_completion_percent(self):
        for record in self:
            wantedItems = 0
            manufacturedItems = 0
            resultsNumbers = []
            for line in record.move_ids_without_package:
                manufacturedItems += line.quantity_done
                wantedItems += line.product_uom_qty
                try:
                    resultsNumbers.append((line.quantity_done / line.product_uom_qty) * 100)
                except ZeroDivisionError:
                    resultsNumbers.append(0)

            try:
                record.completionPercent = int((manufacturedItems / wantedItems) * 100)
            except ZeroDivisionError:
                record.completionPercent = 0
