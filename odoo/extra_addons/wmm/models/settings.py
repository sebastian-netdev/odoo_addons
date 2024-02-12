from odoo import fields, models, api

class WMMSettings(models.Model):
    _name = 'wmm.settings'
    oversize_weigh = fields.Float(string='Set as oversize by weight',default=25.0)
    positions_count = fields.Integer(string='Group positions',default=50)
    orders_count = fields.Integer(string='Group orders',default=8)
    sort_pos_by = fields.Selection([("priority","Priority"),("location","Location")],string='Group by',default='priority')
    picking_type = fields.Many2one('stock.picking.type',string='Picking doc type')
    print_on = fields.Many2one('stock.picking.type',string='Print labels on')
    update_baselinker_status = fields.Boolean(string='Update Baselinker status')
    status_after_packing = fields.Many2one('baselinker.status', string='Status after packing')
    status_after_picking = fields.Many2one('baselinker.status', string='Status after picking')


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'


    oversize_weigh = fields.Float(string='Set as oversize by weight',default=25.0)
    positions_count = fields.Integer(string='Group positions',default=50)
    orders_count = fields.Integer(string='Group orders',default=8)
    sort_pos_by = fields.Selection([("priority","Priority"),("location","Location")],string='Group by',default='priority')
    picking_type = fields.Many2one('stock.picking.type',string='Picking doc type')
    print_on = fields.Many2one('stock.picking.type',string='Print labels on')
    update_baselinker_status = fields.Boolean(string='Update Baselinker status')
    status_after_packing = fields.Many2one('baselinker.status', string='Status after packing')
    status_after_picking = fields.Many2one('baselinker.status', string='Status after picking')

    @api.model
    def get_values(self):
        res = super(ResConfigSettings, self).get_values()
        api = self.env['wmm.settings'].search([('id','=',1)])

        if api:
            res.update(
                oversize_weigh= api.oversize_weigh,
                positions_count = api.positions_count,
                orders_count = api.orders_count,
                sort_pos_by = api.sort_pos_by,
                picking_type = api.picking_type,
                print_on = api.print_on,
                status_after_packing = api.status_after_packing,
                status_after_picking = api.status_after_picking,
                update_baselinker_status = api.update_baselinker_status
            )
        else:
            res.update(
                oversize_weigh= 25,
                positions_count = 50,
                orders_count = 50,
                sort_pos_by = 'priority',
                picking_type = None,
                print_on = None,
                update_baselinker_status = False,
                status_after_packing = None,
                status_after_picking = None,
            )
        return res

    def set_values(self):

        super(ResConfigSettings, self).set_values()

        values = {
            # 'stock_rotation_periods' : self.stock_rotation_periods or None,
            'oversize_weigh': self.oversize_weigh or 25,
            'positions_count': self.positions_count or 50,
            'orders_count': self.orders_count or 50,
            'sort_pos_by': self.sort_pos_by or 'priority',
            'picking_type': self.picking_type.id or None,
            'print_on': self.print_on.id or None,
            'update_baselinker_status': self.update_baselinker_status or False,
            'status_after_picking': self.status_after_picking or None,
            'status_after_packing': self.status_after_packing or None,
        }

        api = self.env['wmm.settings'].search([('id','=',1)])
        if not api:
            self.env['wmm.settings'].create(values)
        else:
            api.write(values)

