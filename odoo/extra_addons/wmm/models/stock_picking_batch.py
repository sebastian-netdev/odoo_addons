from odoo import fields, models, api
from odoo.exceptions import UserError

class StockPickingBatch(models.Model):
    _inherit = 'stock.picking.batch'

    type = fields.Char(string='Type')
    pos_type = fields.Char(string='Pos type',default='unknown')
    courier_module = fields.Char(string='Courier module')

    @api.model
    def set_resposible_user(self):
            if not self.user_id:
                self.user_id = self.env.user.id

    def createbatch(self,type,pos_type,courier_module):

        batch_sequence_code = self.env['ir.sequence'].next_by_code('picking.batch')
        name = None
        if batch_sequence_code is not None:
            batch_name,sequence_id = batch_sequence_code.split('/')
            poscode = 'NO'
            if pos_type == 'onepos':
                poscode = 'JP'
            if pos_type == 'multipos':
                poscode= 'WP'
            typecode= 'N'
            if type == 'normal':
                typecode = 'N'
            if type == 'oversize':
                typecode = 'G'

            if sequence_id is not None:
                name = f'{poscode}/{typecode}/{courier_module}/{sequence_id}'

        values = {
                    'type':type,
                    'pos_type':pos_type,
                    'company_id':self.env.user.company_id.id,
                    'is_wave':False,
                    'state':'draft',
                    'courier_module':courier_module
        }

        if name is not None:
            values['name'] = name

        batch = self.env['stock.picking.batch'].create(values)
        return batch

    def checkstate(self):
        config = self.env['wmm.settings'].search([('id', '=', 1)])
        if config is not None:
            if self.pos_type in ['onepos','unknown']:
                if config.positions_count > 0:
                    if len(self.move_line_ids) >= config.positions_count:
                        self.action_confirm()
            elif self.pos_type =='multipos':


                if config.orders_count and config.orders_count > 0:
                    print(self.name,len(self.picking_ids))
                    if len(self.picking_ids)>=config.orders_count:
                        self.action_confirm()

    def addstockpickingtobatch(self,stockpicking):
        if not stockpicking.batch_id:
            stockpicking.batch_id = self.id

    def managebatchtransfer(self):
        config = self.env['wmm.settings'].search([('id','=',1)])
        picking_doc_type = config.picking_type
        if None not in [config,picking_doc_type]:


            pickings = self.env['stock.picking'].search([('state','=','assigned'),('picking_type_id','=',picking_doc_type.id),('batch_id','=',None)])


            for picking in pickings:

                courier_module = picking.delivery_module

                context = [
                    ('courier_module','=',courier_module),
                    ('pos_type','=',picking.picking_pos_type),
                    ('type','=',picking.dimension),
                    ('state','=','draft'),
                ]

                batch = self.env['stock.picking.batch'].search(context,limit=1)

                if not batch:
                    batch = self.createbatch(picking.dimension,picking.picking_pos_type,courier_module)
                    if batch:
                        batch.addstockpickingtobatch(picking)
                else:
                    batch.addstockpickingtobatch(picking)
                batch.checkstate()
        else:
            raise UserError('Create configuration with picking doc type')

    def action_done(self):
        res = super().action_done()
        trolley = None
        dst_location = None
        config = self.env['wmm.settings'].search([('id', '=', 1)])

        packing_doc_type = config.print_on
        if self.pos_type == 'onepos':
            stock_move_lines = self.move_line_ids
            for stock_move_line in stock_move_lines:
                stock_move_line_dst_loc = stock_move_line.location_dest_id
                if stock_move_line_dst_loc.location_id.is_trolley:
                    trolley = stock_move_line_dst_loc.location_id
                    dst_location = stock_move_line.location_dest_id
                    break
            if trolley not in [False,None,''] and dst_location not in [False,None,'']:
                for stock_move_line in stock_move_lines:
                    stock_move_line.location_dest_id = dst_location
                for picking in self.picking_ids:
                    origin = picking.origin
                    pack_doc = self.env['stock.picking'].search([('origin','=',origin),('picking_type_id','=',packing_doc_type.id)])
                    print(pack_doc)
                    if pack_doc not in [False,None,'']:
                        pack_doc.trolley = trolley
                    else:
                        raise UserError('Packing document for this picking doesnt exist')



            else:
                raise UserError('Wózek lub lokalizacja nie mogą być rozpoznane. ')
            #raise UserError('Testy')
        else:
            raise UserError('Testy2')

        return res