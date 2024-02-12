import datetime

from odoo import fields, models, api
import json



class SaleOrder(models.Model):
    _inherit = 'sale.order'


    def action_confirm(self):
        res = super().action_confirm()
        pickings = self.picking_ids
        for picking in pickings:
            try:
                picking.compute_pos_type(self)
                picking.compute_dimension()
                picking.copy_delivery_method(self.bl_delivery_method)
            except Exception as Error:
                print(Error)
        return res

    def checklabel(self):
        startdate = datetime.datetime.now() - datetime.timedelta(days=1)

        attachments = self.env['ir.attachment'].search(
            [('res_model', '=', 'sale.order'), ('shipping_label', '=', True),('create_date','>',startdate)])

        sale_orders = self.env['sale.order'].search([('create_date','>',startdate)])
        sale_orders_with_attachment = []
        baselinker = self.env['blapi'].search([('id', '>', 0)], limit=1, order='__last_update asc')
        for attachment in attachments:
            sale_orders_with_attachment.append(attachment.res_id)
        for sale_order in sale_orders:

            if sale_order.id not in sale_orders_with_attachment:
                sale_order.get_label(baselinker=baselinker)

    def get_label(self,baselinker = None):
        attachments = self.env['ir.attachment'].search(
            [('res_model', '=', 'sale.order'), ('res_id', '=', self.id), ('shipping_label', '=', True)])
        if len(attachments) == 0:
            delivery_package_nr = self.bl_delivery_package_nr
            delivery_package_module = self.bl_delivery_package_module
            if baselinker is None:
                baselinker = self.env['blapi'].search([('id', '>', 0)], limit=1, order='__last_update asc')
                if len(baselinker) > 0:
                    baselinker = baselinker[0]
                else:
                    baselinker = None

            if delivery_package_nr in [False,'',None] or delivery_package_module in [False,'',None]:
                response = baselinker.getOrderByID(self.name)
                orders = None
                try:
                    result = json.loads(response.text)
                    print(result)
                    orders = result['orders']
                except Exception as Error:
                    print(Error)

                if orders is not None:
                    order_data = orders[0]
                    delivery_package_nr = order_data.get('delivery_package_nr')
                    delivery_package_module = order_data.get('delivery_package_module')
                    if None not in [delivery_package_nr,delivery_package_module]:
                        self.bl_delivery_package_nr = delivery_package_nr
                        self.bl_delivery_package_module = delivery_package_module

            if False not in [delivery_package_nr, delivery_package_module]:
                response = baselinker.getLabels(delivery_package_module, delivery_package_nr)
                label = None
                if isinstance(response,dict):
                    label = response
                if label is not None:
                    name = f'{delivery_package_module}_{delivery_package_nr}'
                    extension = label.get('extension')
                    mimetype = None
                    type = 'binary'
                    label_data = label.get('label')
                    res_model = 'sale.order'
                    res_id = self.id
                    is_shipping = True
                    if extension is not None:
                        if extension == 'pdf':
                            mimetype = 'application/pdf'
                        elif extension == 'zpl':
                            mimetype = 'x-application/zpl'
                    if None not in [extension, mimetype, label]:
                        values = {
                            'name': name,
                            'type': type,
                            'datas': label_data,
                            'display_name': 'Shipping label',
                            'res_model': res_model,
                            'res_id': res_id,
                            'shipping_label': is_shipping,
                            'store_fname': f'{name}.{extension}'
                        }
                        self.env['ir.attachment'].create(values)
