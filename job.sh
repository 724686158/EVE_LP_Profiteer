#!/bin/bash
cd /root/EVE_LP_Profiteer/
python3 /root/EVE_LP_Profiteer/LP_DATA.py
git add -A
git commit -m "data update"
git push -f
