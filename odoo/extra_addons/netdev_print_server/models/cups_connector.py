import base64

import cups
from odoo import api, http, fields, models, tools, SUPERUSER_ID, exceptions, _
from icmplib import ping
import os
from odoo.exceptions import UserError


class PrinterServer(models.Model):
    _name = 'print.server'

    address = fields.Char(string='Server address')
    username = fields.Char(string='Username')
    password = fields.Char(string='Password')
    port = fields.Integer(string='TCP port', default=631)
    status = fields.Selection([('1', 'available'), ('0', 'not available')],string='Printer server status', default='0')


    def check_status(self):
        settings = self.env['print.server'].search([('id', '=', 1)])
        if settings:
            count = 4
            result = ping(settings.address, count=count,privileged=False)
            if result.packets_received == count:
                settings.status = '1'
            else:
                settings.status = '0'



    def create_connection(self):
        settings = self.env['print.server'].search([('id','=',1)])

        if settings:
            cups.setServer(settings.address)
            cups.setPort(settings.port)
            if False not in [settings.username,settings.password]:
                cups.setUser(settings.username)
                cups.setPasswordCB(settings.password)
            try:
                conn = cups.Connection()
            except RuntimeError as Error:
                raise UserError(f'Error: {Error}')

            if conn is not None:
                return conn

    def registerprinters(self):
        print_server = self.env['print.server'].search([('id', '=', 1)])
        conn = self.create_connection()
        cups_printers = conn.getPrinters()
        for cups_printer in cups_printers:
            if len(cups_printer) > 3:
                printer = self.env['printer'].search([('name','=',cups_printer)])
                if not printer:
                    cups_printer_data = cups_printers.get(cups_printer)
                    printer_status = cups_printer_data.get('printer-state')
                    if printer_status is not None:
                        printer_status = str(printer_status)
                    printer_message = cups_printer_data.get('printer-state-message')
                    if None not in [printer,printer_status,cups_printer_data]:
                        values = {
                            'name':cups_printer,
                            'server': print_server.id,
                            'status': printer_status,
                            'message': printer_message,
                        }
                        self.env['printer'].create(values)

                else:
                    cups_printer_data = cups_printers.get(cups_printer)
                    printer_status = cups_printer_data.get('printer-state')
                    printer_message = cups_printer_data.get('printer-state-message')
                    if printer_message is not None:
                        printer.message = printer_message

                    if printer_status:
                        printer.status = str(printer_status)


class Printer(models.Model):
    _name = 'printer'

    name = fields.Char(string='Printer name')
    server = fields.Many2one('print.server', string='Print server')
    status = fields.Selection([('3','idle'),('4','busy'),('5','stopped')],string='Printer status')
    message = fields.Char(string='Printer message')
    user = fields.Many2one('res.users', string='User', default=None,domain=[('share', '=', False)])


    @api.constrains('user')
    def _check_user(self):
        if self.user:
            user_printer = self.env['printer'].search([('user','=',self.user.id)])
            if len(user_printer) > 1:
                raise UserError('This user has assigned printer. Please remove existing one first. ')



    def printfile(self,file):

        if file is not None:
            file_raw = file.raw
            filename = file.display_name
            extension = None
            if file.mimetype == 'application/pdf':
                extension = 'pdf'

            path = f'/tmp/{filename}.{extension}'
            newfile = open(path, 'wb')
            newfile.write(file_raw)

            settings = self.env['print.server'].search([('id', '=', 1)])
            conn = settings.create_connection()
            conn.printFile(self.name, path, file.display_name, {})

            os.remove(path)





