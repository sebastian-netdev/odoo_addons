from odoo.addons.stock_barcode.controllers.stock_barcode import StockBarcodeController
from odoo.exceptions import UserError
from odoo import http, _
from odoo.http import request
from odoo import api

class StockBarcodeControllerExtender(StockBarcodeController):

    @http.route('/stock_barcode/get_barcode_data', type='json', auth='user')
    def get_barcode_data(self,model,res_id):
        res = super().get_barcode_data(model,res_id)
        if model == 'stock.picking.batch':
            stock_picking_batch = request.env[model].search([('id','=',res_id)])
            if stock_picking_batch:
                stock_picking_batch.set_resposible_user()

        return res
