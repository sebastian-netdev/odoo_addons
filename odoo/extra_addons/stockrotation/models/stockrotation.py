from odoo import fields,models,api
import datetime
import logging
_logger = logging.getLogger(__name__)
class ProductTemplate(models.Model):
    _inherit = 'product.product'

    est_rotation = fields.Integer(string='Estimated rotation(days)', default=60)
    real_rotation = fields.Integer(string='Real rotation(days)', default=0)
    dst_rotation = fields.Float(default=0,string='Dst daily rotation rate')
    daily_rotation = fields.Float(string='Real daily rotation rate', default=0)
    stock_after_period = fields.Float(string='Stock after 14 days', default=0)
    last_update = fields.Date(string='Last update',default=datetime.datetime(2023,2,10))
    first_delivery = fields.Date(string='First delivery',default=datetime.datetime(2023,2,10))
    sales_last_period = fields.Integer(string=f'Sales last period', default=0)
    total_income = fields.Integer(string = 'Total incoming qty',default=0)
    total_outgoing = fields.Integer(string = 'Total outgoing qty',default=0)
    avg_margin = fields.Float(string='Total avg margin',default=0)
    avg_margin_period = fields.Float(string=f'Avg margin est rotation',default=0)
    total_sale_amount = fields.Float(string='Total sale amount',defualt=0)
    product_tags = fields.Many2many('product.tag',string='Product tags')
    product_active = fields.Boolean(string='Product active', default=True)

    def initalizestockrotationupdate(self,records=None, limit=None):
        if limit is not None and isinstance(limit,int):
            limit = limit
        else:
            limit = 1000

        if records is None:
            products = self.env['product.product'].search([('id','>','0'),('product_active','=',True)],order='last_update asc',limit=limit)
            _logger.info(str(len(products)))
            for product in products:
                if product.product_active:
                    self.updateadditionalparams(product)
                    product.last_update = datetime.datetime.now()
        else:
            for record in records:
                if record.product_active:
                    self.updateadditionalparams(record)
                    record.last_update = datetime.datetime.now()
    def forecaststockperiod(self,limit=None):
        if limit is not None and isinstance(limit,int):
            limit = limit
        else:
            limit = 1000


        records = self.env['product.product'].search([('id', '>', '0'), ('product_active', '=', True)],
                                                      order='last_update asc',limit=limit)
        for record in records:
            self.calulatedailyrotationperiod(record)

    def checkproductactivity(self):
        products = self.env['product.product'].search([('id', '>', '0')])
        config = self.env['stockrotation.settings'].search([('id', '=', 1)])
        for product in products:
            if product.qty_available > 0:
                product.product_active = True
            else:
                last_move_out = self._findlastmoveout(product)
                if last_move_out:
                    diff = datetime.datetime.now() - last_move_out
                    if config.inactive_delay:
                        if diff.days > config.inactive_delay:
                            product.product_active = False
                        else:
                            product.product_active = True
                else:
                    product.product_active = False


    def updateadditionalparams(self,record):
        try:
            self._computedstrotation(record)
            self._computerealrotation(record)
            self._findfirstdelivery(record)
            #self._computetotalquantities(record)
            self._productavgmargintotal(record)
            self._productavgmarginperiod(record)
        except Exception as Error:
            _logger.error(f'{Error}:{record}')

    def _computedstrotation(self,record):
        last_delivery = self._findlastdelivery(record)
        config = self.env['stockrotation.settings'].search([('id', '=', 1)])
        if last_delivery is not None:
            if record.est_rotation > 0:
                est_rotation = record.est_rotation
            else:
                est_rotation = config.stock_rotation

            last_delivery_lines = self.env['stock.move.line'].search(
                [('product_id', '=', record.id),('picking_id', '=', last_delivery.id),
                 ('state', '=', 'done'),
                 ('picking_id.picking_type_id.sequence_code', '=', 'PZ')],
                order='date desc')


            last_delivery_quantity = 0
            for last_delivery_line in last_delivery_lines:
                last_delivery_quantity += last_delivery_line.qty_done

            try:
                record.dst_rotation = last_delivery_quantity / est_rotation
            except ZeroDivisionError:
                record.dst_rotation = 0


    def _computerealrotation(self,record):
        config = self.env['stockrotation.settings'].search([('id', '=', 1)])
        last_delivery = self._findlastdelivery(record)
        start_date = None
        end_date = None
        if last_delivery is not None:
            end_date = last_delivery.date
        else:
            end_date = datetime.datetime(2023,2,10)

        if record.qty_available > 0:
            start_date = datetime.datetime.today()
        else:
            last_move_out = self._findlastmoveout(record)
            if last_move_out is not None:
                start_date = last_move_out

        if None not in [start_date,end_date]:

            diff = start_date - end_date
            diff_days = diff.days


            stock_outs = self.env['stock.move.line'].search([('product_id', '=', record.id), ('date', '>', end_date),('date', '<', start_date),
                                                               ('state', '=', 'done'),
                                                               ('picking_id.picking_type_id.sequence_code', '=', 'WZ')],
                                                          order='date desc')
            quantity_out = 0
            for stock_out in stock_outs:
                quantity_out += stock_out.qty_done

            try:
                record.daily_rotation = quantity_out / diff_days
            except ZeroDivisionError:
                record.record.daily_rotation = 0


            if diff_days > 14 or record.qty_available == 0:
                if config.low_stock_rotation_tag is not None:
                    if record.daily_rotation < (record.dst_rotation * 0.85):
                        record.product_tags = [(4,config.low_stock_rotation_tag.id)]
                    else:
                        record.product_tags = [(3, config.low_stock_rotation_tag.id)]

                if config.high_stock_rotation_tag is not None:
                    if record.daily_rotation > (record.dst_rotation * 1.15):
                        record.product_tags = [(4,config.high_stock_rotation_tag.id)]
                    else:
                        record.product_tags = [(3, config.high_stock_rotation_tag.id)]
            else:
                record.product_tags = [(3, config.low_stock_rotation_tag.id)]
                record.product_tags = [(3, config.high_stock_rotation_tag.id)]


    def calulatedailyrotationperiod(self,record):
        config = self.env['stockrotation.settings'].search([('id', '=', 1)])
        newdate = datetime.datetime.now() - datetime.timedelta(days=config.rotation_period)
        newdate = datetime.datetime(year=newdate.year, month=newdate.month, day=newdate.day)

        stock_move_lines = self.env['stock.move.line'].search([('product_id', '=', record.id), ('date', '>', newdate),
                                                               ('state', '=', 'done'),
                                                               ('picking_id.picking_type_id.sequence_code', '=', 'WZ')],
                                                              order='date desc')
        quantity = 0
        for stock_move_line in stock_move_lines:
            quantity = quantity + stock_move_line.qty_done

        record.stock_after_period = record.virtual_available - quantity
        record.sales_last_period = quantity




    def _findlastdelivery(self,record):
        last_delivery = None
        last_delivery_date = datetime.datetime(2023,2,10)
        stock_move_lines = self.env['stock.move.line'].search([('product_id', '=', record.id), ('state', '=', 'done'),
                                                               ('picking_id.picking_type_id.sequence_code', '=', 'PZ')],
                                                              order='date desc', limit=1)

        for stock_move_line in stock_move_lines:
            if stock_move_line.date > last_delivery_date:
                last_delivery = stock_move_line.picking_id
        return last_delivery





    def _findfirstdelivery(self,record):
            first_delivery = datetime.datetime(2023,2,10)
            stock_move_lines = self.env['stock.move.line'].search([('product_id','=',record.id),('state','=','done'),('picking_id.picking_type_id.sequence_code','=','PZ')],order='date asc',limit=1)

            for stock_move_line in stock_move_lines:
                if stock_move_line.date > first_delivery:
                    first_delivery = stock_move_line.date

            record.first_delivery = first_delivery


    def computetotalquantities(self,records):
        for record in records:
            stockin = None
            stockout = None
            startdate = None
            stockoutdate = None


            stockin = self.env['stock.move.line'].search([('product_id', '=', record.id), ('state', '=', 'done'),
                                                                   ('picking_id.picking_type_id.sequence_code', '=', 'PZ')],
                                                                  order='date desc')

            stockout = self.env['stock.move.line'].search([('product_id', '=', record.id), ('state', '=', 'done'),
                                                                   ('picking_id.picking_type_id.sequence_code', '=', 'WZ')],
                                                                  order='date desc')


            totalquantityin = 0
            for element in stockin:
                totalquantityin += element.qty_done

            totalquantityout = 0
            for element in stockout:
                totalquantityout += element.qty_done

            record.total_income = totalquantityin
            record.total_outgoing = totalquantityout



    def _findlastmoveout(self,record):
        last_sale = None
        stock_move_lines = self.env['stock.move.line'].search([('product_id', '=', record.id), ('state', '=', 'done'),
                                                               ('picking_id.picking_type_id.sequence_code', '=', 'WZ')],
                                                              order='date desc', limit=1)
        for stock_move_line in stock_move_lines:
            if last_sale is None:
                last_sale = stock_move_line.date
            elif stock_move_line.date > last_sale:
                last_sale = stock_move_line.date

        return last_sale

    


    # @api.depends('last_sale')
    # def _nomove(self, record):
    #     if record.qty_available > 0:
    #         today = datetime.datetime.now()
    #         last_sale_date = record.last_sale
    #         last_sale = datetime.datetime(last_sale_date.year, last_sale_date.month, last_sale_date.day)
    #         diff = today - last_sale
    #         if diff.days > 14:
    #             record.low_rotation = True
    #         else:
    #             record.low_rotation = False

    def _productavgmargintotal(self,record):
        order_lines = self.env['sale.order.line'].search([('product_id', '=', record.id)],
                                                              order='create_date desc')
        counter = 0
        margin = 0
        if len(order_lines) > 0:
            for order_line in order_lines:
                counter += 1
                margin += order_line.margin_percent
            record.avg_margin = margin/counter

    def _productavgmarginperiod(self, record):

        newdate = datetime.datetime.now() - datetime.timedelta(days=record.est_rotation)

        newdate = datetime.datetime(newdate.year, newdate.month, newdate.day)

        order_lines = self.env['sale.order.line'].search(
            [('product_id', '=', record.id),('create_date', '>', newdate)],
            order='create_date desc')
        counter = 0
        margin = 0
        if len(order_lines)>0:
            for order_line in order_lines:
                counter += 1
                margin += order_line.margin_percent
            record.avg_margin_period = margin / counter



class SaleOrder(models.Model):
    _inherit = 'sale.order'


    def checkmargin(self):
        newdate = datetime.datetime.now() - datetime.timedelta(days=1)

        orders = self.env['sale.order'].search([('create_date','>',newdate), ('state', '=', 'sale')])

        baselinkerconfig = self.env['baselinker.config.settings'].search([('id', '=', 1)])
        config = self.env['stockrotation.settings'].search([('id', '=', 1)])

        for order in orders:

            margin_value = 0
            orderlines = self.env['sale.order.line'].search([('order_id','=',order.id)])
            if len(orderlines) > 0:
                counter = len(orderlines)
                for orderline in orderlines:
                    if orderline.product_id is not None:
                        if orderline.product_id.id != baselinkerconfig.baselinker_delivery_product_id.id:
                            margin_value = margin_value + orderline.margin_percent
                            order_line_margin = orderline.margin_percent
                            if order_line_margin < config.low_margin_level:
                                orderline.order_line_tags = [(4, config.low_margin_tag.id)]
                            else:
                                orderline.order_line_tags = [(3, config.low_margin_tag.id)]

                            if order_line_margin < config.high_margin_level:
                                orderline.order_line_tags = [(4, config.high_margin_tag.id)]
                            else:
                                orderline.order_line_tags = [(3, config.high_margin_tag.id)]
                        else:
                            counter = counter - 1
                try:
                    avg_margin = margin_value/counter


                    if avg_margin < config.low_margin_level:
                        order.tag_ids = [(4, config.low_margin_tag.id)]
                    else:
                        order.tag_ids = [(3, config.low_margin_tag.id)]

                    if avg_margin > config.high_margin_level:
                        order.tag_ids = [(4, config.high_margin_tag.id)]
                    else:
                        order.tag_ids = [(3, config.high_margin_tag.id)]

                except ZeroDivisionError:
                    pass



class SaleOrderLine(models.Model):
    _inherit = 'sale.order.line'
    order_line_tags = fields.Many2many('crm.tag', string='Order line tags')

class StockRotationSettins(models.Model):
    _name = 'stockrotation.settings'
    #stock_rotation_periods = fields.Char(string="Stock rotation periods")
    stock_rotation = fields.Integer(string="Default rotation(days)",default=60)
    rotation_period = fields.Integer(string="Rotation period(days)",default=14)
    low_stock_rotation_notify = fields.Boolean(string="Notify if stock rotation low", default=False)
    high_stock_rotation_notify = fields.Boolean(string="Notify if stock rotation high", default=False)
    low_margin_level = fields.Float(string="Low margin level",default=0.2)
    high_margin_level = fields.Float(string="High margin level",default=0.5)
    low_margin_alert = fields.Boolean(string="Notify if low margin level reached",default=False)
    notification_address = fields.Many2one('res.users','User for notification',domain=[('share', '=', False)])
    low_stock_rotation_tag = fields.Many2one('product.tag','Tag used for low stock rotation')
    high_stock_rotation_tag = fields.Many2one('product.tag','Tag used for high stock rotation')
    low_margin_tag = fields.Many2one('crm.tag','Tag used for low margin')
    high_margin_tag = fields.Many2one('crm.tag', 'Tag used for high margin')
    inactive_delay = fields.Integer(string='Set as inactive after(days)',default=365)


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    #stock_rotation_periods = fields.Char(string="Stock rotation periods")
    stock_rotation = fields.Integer(string="Default rotation(days)",default=60)
    rotation_period = fields.Integer(string="Rotation period(days)",default=14)
    inactive_delay = fields.Integer(string="Set as inactive after(days)",default=365, help='This variable is...')
    low_stock_rotation_notify = fields.Boolean(string="Notify if stock rotation low", default=False)
    high_stock_rotation_notify = fields.Boolean(string="Notify if stock rotation high", default=False)
    low_margin_level = fields.Float(string="Low margin level")
    high_margin_level = fields.Float(string="High margin level")
    low_margin_alert = fields.Boolean(string="Notify if low margin level reached")
    notification_address = fields.Many2one('res.users','User for notification',domain=[('share', '=', False)])
    low_stock_rotation_tag = fields.Many2one('product.tag','Tag used for low stock rotation')
    high_stock_rotation_tag = fields.Many2one('product.tag','Tag used for high stock rotation')
    low_margin_tag = fields.Many2one('crm.tag','Tag used for low margin')
    high_margin_tag = fields.Many2one('crm.tag','Tag used for high margin')

    @api.model
    def get_values(self):
        res = super(ResConfigSettings, self).get_values()
        api = self.env['stockrotation.settings'].search([('id', '=', 1)])
        res.update(
            #stock_rotation_periods=api.stock_rotation_periods,
            stock_rotation=api.stock_rotation,
            rotation_period=api.rotation_period,
            low_stock_rotation_notify=api.low_stock_rotation_notify,
            high_stock_rotation_notify=api.high_stock_rotation_notify,
            low_margin_level=api.low_margin_level,
            high_margin_level=api.high_margin_level,
            low_margin_alert=api.low_margin_alert,
            notification_address=api.notification_address,
            low_stock_rotation_tag=api.low_stock_rotation_tag,
            high_stock_rotation_tag=api.high_stock_rotation_tag,
            low_margin_tag=api.low_margin_tag,
            high_margin_tag=api.high_margin_tag,
            inactive_delay=api.inactive_delay,
        )
        return res


    def set_values(self):
        super(ResConfigSettings, self).set_values()

        values = {
           # 'stock_rotation_periods' : self.stock_rotation_periods or None,
            'stock_rotation' : self.stock_rotation or 60,
            'rotation_period' : self.rotation_period or 14,
            'low_stock_rotation_notify' : self.low_stock_rotation_notify or False,
            'high_stock_rotation_notify' : self.high_stock_rotation_notify or False,
            'low_margin_level' : self.low_margin_level or 0.2,
            'high_margin_level' : self.high_margin_level or 0.5,
            'notification_address' : self.notification_address or None,
            'low_stock_rotation_tag' : self.low_stock_rotation_tag or None,
            'high_stock_rotation_tag' : self.high_stock_rotation_tag or None,
            'low_margin_tag' : self.low_margin_tag or None,
            'high_margin_tag' : self.high_margin_tag or None,
            'inactive_delay' : self.inactive_delay or 365,
        }


        # self.env['ir.config_parameter'].set_param("stockrotation_stock_rotation_periods", self.stock_rotation_periods or '')
        # self.env['ir.config_parameter'].set_param("stockrotation_stock_rotation", self.stock_rotation or 0)
        # self.env['ir.config_parameter'].set_param("stockrotation_low_stock_rotation_notify", self.low_stock_rotation_notify or False)
        # self.env['ir.config_parameter'].set_param("stockrotation_high_stock_rotation_notify", self.high_stock_rotation_notify or False)
        # self.env['ir.config_parameter'].set_param("stockrotation_low_margin_level", self.low_margin_level or 0.2)
        # self.env['ir.config_parameter'].set_param("stockrotation_low_margin_alert", self.low_margin_alert or False)
        # self.env['ir.config_parameter'].set_param("stockrotation_notification_address", self.notification_address or None)
        # self.env['ir.config_parameter'].set_param("stockrotation_low_stock_rotation_tag", self.low_stock_rotation_tag or None)
        # self.env['ir.config_parameter'].set_param("stockrotation_high_stock_rotation_tag", self.high_stock_rotation_tag or None)
        # self.env['ir.config_parameter'].set_param("stockrotation_low_margin_tag", self.low_margin_tag or None)
        # self.env['ir.config_parameter'].set_param("stockrotation_high_margin_tag", self.low_margin_tag or None)

        api = self.env['stockrotation.settings'].search([('id','=',1)])
        if not api:
            self.env['stockrotation.settings'].sudo().create(values)
        else:
            api.sudo().write(values)



class ProductTag(models.Model):
    _name = 'product.tag'
    _inherit = 'crm.tag'






