import time

from odoo import fields, models, api
from odoo.exceptions import UserError


class StockPicking(models.Model):
    _inherit = 'stock.picking'

    delivery_module = fields.Char(string='Delivery module',default=None)
    picking_pos_type = fields.Char(string='Picking pos status', default='unknown')
    dimension = fields.Char(string='Dimension', default='normal',translate=True)
    trolley = fields.Many2one('stock.location',string='Trolley',default=None,context="[('is_trolley','=',True)]")
    trolley_barcode = fields.Char(string='Wózek',related='trolley.barcode')
    type_id_num = fields.Integer(default=1)
    is_packing = fields.Boolean(string='is packing',compute='_calculate_type',default=False)

    def _calculate_type(self):
        config = self.env['wmm.settings'].search([('id', '=', 1)])
        packing = config.print_on
        for record in self:
            if record.picking_type_id.id == packing.id:
                record.is_packing = True
            else:
                record.is_packing = False

    def compute_dimension(self):
        self.dimension = 'normal'
        if self.weight >= 25:
            self.dimension = 'oversize'

    def copy_delivery_method(self,bl_delivery_method):
        shipping_mapper = self.env['shipping.mapper'].search([('name', '=', bl_delivery_method)])
        if not shipping_mapper:
            values = {
                'name': bl_delivery_method,
                'shipping': None
            }
            self.env['shipping.mapper'].create(values)
            self.delivery_module = bl_delivery_method
        else:
            if shipping_mapper.shipping:
                if shipping_mapper.shipping.short_name not in [False, None, '']:
                    self.delivery_module = shipping_mapper.shipping.short_name
                else:
                    self.delivery_module = shipping_mapper.shipping.name

    def compute_pos_type(self,sale_order):
        self.picking_pos_type = 'unknown'
        order_lines_list = []

        if sale_order:
            order_lines = sale_order.order_line
            for index, order_line in enumerate(order_lines):
                if order_line.product_id.detailed_type == 'product':
                    order_lines_list.append(order_line)

            if len(order_lines_list) > 1:
                self.picking_pos_type = 'multipos'
            elif len(order_lines_list) == 1:

                if order_lines_list[0].product_qty > 1:
                    self.picking_pos_type = 'multipos'
                else:
                    self.picking_pos_type = 'onepos'

    def printlabel(self):
        user = self.env.user
        printer = None
        if user:
            printer = self.env['printer'].search([('user','=',user.id)])
        saleorder = self.env['sale.order'].search([('name','=',self.origin)])

        if len(printer) == 1 and printer:
            file = self.env['ir.attachment'].search(
                [('res_model', '=', 'sale.order'), ('res_id', '=', saleorder.id), ('shipping_label', '=', True)])
            if file:
                printer.printfile(file)
            else:
                raise UserError('Label for this order doesn\'t exist ')

        else:
            raise UserError('Printer for this user doeasn\'t exist')

    def button_validate(self):
        res = super().button_validate()
        config = self.env['wmm.settings'].search([('id','=',1)])

        if config:
            print_on_picking = config.print_on
            if print_on_picking:
                if self.picking_type_id.id == print_on_picking.id:
                    self.printlabel()

                if config.update_baselinker_status:
                    if config.status_after_packing:
                        baselinker = self.env['baselinker']
                        baselinker.setOrderStatus(self.origin, config.status_after_picking.bl_id)
            if self.picking_type_id.id == config.picking_type.id:
                if config.update_baselinker_status:
                    if config.status_after_picking:
                        baselinker = self.env['baselinker']
                        baselinker.setOrderStatus( self.origin, config.status_after_picking.bl_id)
        else:
            raise UserError('Configuration doesn\'t exist')
        return res


    def check_availability(self):
        config = self.env['wmm.settings'].search([('id', '=', 1)])
        if config:
            picking_doc_type = config.picking_type
            stock_pickings = self.env['stock.picking'].search([('state','=','confirmed'),('picking_type_id','=',picking_doc_type.id)])

            for element in stock_pickings:
                element.action_assign()
