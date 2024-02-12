#!/bin/sh
# by warp3r(2022)
#
# Użytkownik Odoo
ODOOUSER='odoo'
# czyszczenie Python Cache
sudo rm -rf `find . -name "__pycache__"`
#
# nadanie uprawnien do zasobow uzytkoniwkowi odoo
sudo chown -R $ODOOUSER:$ODOOUSER .
#
# poprawienie uprawnien do plikow i katalogow
sudo chmod 775 $(find . -type d)
sudo chmod 664 $(find . -type f)
sudo chmod +x commit.sh
#
# aktualizacja repozytorium
git add .
git commit -m "normal commit:: $1"
git push -u origin master
#
#EOF


