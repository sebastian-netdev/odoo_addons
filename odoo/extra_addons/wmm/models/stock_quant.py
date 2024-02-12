
from odoo.exceptions import UserError
from odoo import fields, models, api



class StockQuant(models.Model):

    _inherit = 'stock.quant'



    def apply_inventory(self):
        stock_quants = self.env['stock.quant'].search([('inventory_quantity','>',0)])
        print(len(stock_quants))
        counter = 0
        for stock_quant in stock_quants:

            print(stock_quant.inventory_quantity)
            result = stock_quant.action_apply_inventory()
            if result is None:
                counter += 1
            if counter >=200:
                break
            print(counter)

