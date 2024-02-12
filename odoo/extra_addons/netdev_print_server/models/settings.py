from odoo import fields, models, api

class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    print_server_address = fields.Char(string='Print server address',default=None)
    print_server_port = fields.Integer(string='Print server port', default=631)
    print_server_username = fields.Char(string='Print server username', default=None)
    print_server_password = fields.Char(string='Print server password', default=None)
    print_server_status = fields.Selection([('1','available'),('0','not available')],string='Printer server status')

    @api.model
    def get_values(self):
        res = super(ResConfigSettings, self).get_values()
        api = self.env['print.server'].search([('id', '=', 1)])
        if len(api) > 0:
            res.update(
                # stock_rotation_periods=api.stock_rotation_periods,
                print_server_address=api.address,
                print_server_port=api.port,
                print_server_username=api.username,
                print_server_password=api.password,
                print_server_status=api.status,

            )

        else:
            res.update(
                print_server_address=None,
                print_server_port=631,
                print_server_username=None,
                print_server_password=None,
                print_server_status=None,
            )

        return res

    def set_values(self):

        super(ResConfigSettings, self).set_values()

        values = {
           # 'stock_rotation_periods' : self.stock_rotation_periods or None,
            'address' : self.print_server_address or None,
            'port' : self.print_server_port or 631,
            'username' : self.print_server_username or None,
            'password' : self.print_server_password or None,
        }

        api = self.env['print.server'].search([('id','=',1)])
        if not api:
            self.env['print.server'].sudo().create(values)
        else:
            api.sudo().write(values)