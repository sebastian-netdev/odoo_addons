from odoo import fields, models, api
import logging

_logger = logging.getLogger(__name__)

class SaleOrder(models.Model):
    _inherit = 'sale.order'

    def action_confirm(self):
        res = super().action_confirm()
        if self.picking_ids:
            for stock_picking in self.picking_ids:
                isAllOnWarehouse = True
                for line in stock_picking.move_ids_without_package:
                    if line.product_uom_qty > line.product_id.qty_available:                   
                        isAllOnWarehouse = False

                if isAllOnWarehouse and stock_picking.state != "done":
                    stock_picking.action_set_quantities_to_reservation()
                    stock_picking.button_validate()
        return res