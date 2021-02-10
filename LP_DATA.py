import pandas as pd
import numpy as np
import re
import requests
import time
import ssl
import json
import openpyxl
import lxml

ssl._create_default_https_context = ssl._create_unverified_context

pd.set_option('display.max_rows', None)
pd.set_option('display.max_columns', None)
pd.set_option('display.width', 2000)
pd.set_option('display.max_colwidth', 1000)

#数据获取与预处理
def get_marketdata(url, base):

    def get_all_data(all_items):
        url = 'https://evepraisal.com/appraisal/structured.json'
        data = {
            "market_name": "jita",
            "items": []
        }
        for name in all_items:
            data["items"].append({'name': name})
        # print(data)
        r = requests.request(url=url, method="POST", data=json.dumps(data))
        # print(r.text)
        if r.status_code == 200:
            for item in r.json().get('appraisal').get('items'):
                price = float(item.get("prices").get("sell").get("min"))
                print("[JITA SELL]{}:{:,.2f}".format(item["name"], price))
            return r.json().get('appraisal').get('items')
        else:
            return None

    def get_sell_price(all_data, item_name):
        for item in all_data:
            if item["name"] == item_name:
                return float(item.get("prices").get("sell").get("min"))
        return np.nan


    def get_buy_price(all_data, item_name):
        for item in all_data:
            if item["name"] == item_name:
                return float(item.get("prices").get("buy").get("max"))
        return np.nan

    # 获取当前天蛇LP市场行情
    tables = pd.read_html(url)
    lpmarket = tables[0].dropna()
    # 清理无效行
    lpmarket_swap = lpmarket.copy()
    lpmarket_swap['LP'] = lpmarket['LP'].apply(lambda x: x if re.search("^[1-9]\d*$", str(x)) else np.nan)
    lpmarket = lpmarket_swap.dropna()

    lpmarket_swap = lpmarket.copy()
    lpmarket_swap['Other Requirements'] = lpmarket['Other Requirements'].apply(
        lambda x: np.nan if re.search("^[1-9]\d*$", str(x)) else x)
    lpmarket = lpmarket_swap.dropna()

    lpmarket_swap = lpmarket.copy()
    lpmarket_swap['Other Requirements'] = lpmarket['Other Requirements'].replace(
        'LP Store1|LP Store10|LP Store5000|LP Store', '', regex=True)
    lpmarket = lpmarket_swap.dropna()

    # 类型转换
    lpmarket_swap = lpmarket.copy()
    lpmarket_swap['LP'] = lpmarket['LP'].apply(int)
    lpmarket_swap['id'] = lpmarket['id'].apply(int)
    lpmarket_swap['5% Volume'] = lpmarket['5% Volume'].apply(int)
    lpmarket = lpmarket_swap

    # 并按照LP单价进行排序
    lpmarket = lpmarket.sort_values(by='isk/lp', ascending=False)

    # 设置一个阈值，该阈值是允许的LP最低单位数，如最低只兑换 低蛇Alpha，每个 3750 LP,再低级的兑换品如弹药则不予讨论
    low_bound = 3750
    lpmarket = lpmarket[lpmarket['LP'] >= low_bound]

    # # 设置一个阈值，该阈值是允许的LP最低均价，只采购比例高于此值的产品
    # avg_low_bound = 2700
    # lpmarket = lpmarket[lpmarket['isk/lp'] >= avg_low_bound]

    # 以下通过分析卖单价进行排序
    print("!!!!!!")
    print(lpmarket['Item'].tolist())
    all_data = get_all_data(lpmarket['Item'].tolist())
    lpmarket["Sell Price"] = lpmarket['Item'].apply(lambda x: get_sell_price(all_data, x))
    lpmarket["REAL BUY Price"] = lpmarket['Item'].apply(lambda x: get_buy_price(all_data, x))
    lpmarket["Buy isk/lp"] = (lpmarket["REAL BUY Price"] - lpmarket["Other Cost"] * lpmarket["Quantity"] - lpmarket["Isk"]) / lpmarket["LP"]
    lpmarket["Sell isk/lp"] = (lpmarket["Sell Price"] - lpmarket["Other Cost"] * lpmarket["Quantity"] - lpmarket["Isk"]) / lpmarket["LP"]
    # 依次获取商品价格
    lpmarket = lpmarket.sort_values(by='Sell isk/lp', ascending=False)
    # 重建索引
    lpmarket = lpmarket.set_index('Item')

    #打印市场行情总览
    print("市场行情:")
    print(lpmarket)
    # 保存市场行情
    lpmarket = lpmarket.sort_values(by='Buy isk/lp', ascending=False)
    writer = pd.ExcelWriter('market_data/{}/{}_NEW.xlsx'.format(base, base))
    lpmarket.to_excel(writer, 'Sheet1')
    writer.save()
    return lpmarket

#从文件中获取市场行情(测试用）
def get_marketdata_from_file(filename):
    xlsx = pd.ExcelFile('market_data/' + filename)
    marketdata = pd.read_excel(xlsx, 'Sheet1')
    marketdata = marketdata.set_index('Item')
    return marketdata

#获取收购计划
def get_order_plan(file_name, time = 1):
    xlsx  = pd.ExcelFile('order_plans/' + file_name)
    order_plan = pd.read_excel(xlsx, 'Sheet1')
    order_plan['Qty'] = order_plan['Qty'] * time
    order_plan = order_plan.set_index('Item')
    return order_plan

#根据市场行情，订单计划，LP价格，三种信息提供交易辅助
def trade_aide(marketdata, orderplan, lpprice):
    #打印市场行情
    print("打印市场行情：")
    print(marketdata)
    #打印采购计划
    print("打印采购计划：")
    print(orderplan[['Qty']])
    while True:
        print("输入收LP数量：", end="")
        lpsum = int(input())

        # 多重背包求解
        itemdata = pd.DataFrame({'Value': [], 'LP': [], 'Qty': []})
        itemdata['LP'] = orderplan['LP']
        itemdata['Qty'] = orderplan['Qty']
        itemdata['Value'] = (marketdata['Sell isk/lp'] - lpprice) * marketdata['LP']

        # 剔除负价值物品
        itemdata = itemdata[itemdata['Value'] >= 0]

        def MultiPack(itemdata, maxLp):

            order = pd.DataFrame({'Item': []})

            # 缩减问题规模
            V = int(maxLp / 100)
            itemdata['LP'] = (itemdata['LP'] + 50) / 100
            index_list = itemdata.index
            v = itemdata['Value'].apply(int).tolist()
            w = itemdata['LP'].apply(int).tolist()
            t = itemdata['Qty'].astype(int).tolist()

            #最多可以分析500W LP的动归问题
            dp = pd.DataFrame(np.zeros((100, 50001)))

            n = int(str(itemdata.shape[0]))

            for i in range(n):
                for j in range(w[i] , V+1):
                    count = int(min(t[i], j / w[i]))
                    tmp = 0
                    for k in range(0, count+1):
                        if dp.at[i, j - w[i] * k] + v[i] * k > tmp:
                            tmp = dp.at[i, j - w[i] * k] + v[i] * k
                    dp.at[i + 1, j] = max(dp.at[i, j], tmp)

            c = V
            x = [0] * n
            for i in range(0, n)[::-1]:
                for j in range(0, min(int(V / w[i] + 1), t[i] + 1)):
                    if dp.at[i + 1, c] == dp.at[i, c - j * w[i]] + j * v[i]:
                        x[i] = j
                        c = c - j * w[i]
                        break
                    else:
                        x[i] = 0
            # print("本次交易预计收益约为：{}".format(dp.at[n, V]))
            for i in range(0, n):
                item_name = index_list[i]
                order = order.append(
                    [{'Item': item_name}] * x[i],
                    ignore_index=True)
                orderplan.at[index_list[i], 'Trade Count'] = x[i]
            return order

        order = MultiPack(itemdata, lpsum)

        # 打印订单详情
        print("打印订单详情")
        print(order)

        # 求购商品列表
        print("兑换产品列表：")
        print(order['Item'].value_counts().to_string())
        order = order.set_index('Item')
        order['LP'] = None
        order['Other Requirements'] = None
        order['Isk'] = None
        for i in order.index:
            order.at[i, 'LP'] = marketdata.at[i, 'LP']
            order.at[i, 'Other Requirements'] = marketdata.at[i, 'Other Requirements']
            order.at[i, 'Isk'] = marketdata.at[i, 'Isk']

        # 需提供原材料列表
        print("原材料列表：")
        print(order['Other Requirements'].value_counts().to_string())
        # 提供兑换所需ISK
        print("兑换所需ISK：")
        print('{:,.2f}'.format(order['Isk'].sum()))
        # 实际兑换LP数量
        print("实际兑换LP数量:")
        print('{:,.2f}'.format(order['LP'].sum()))
        # 支付LP报酬
        print("支付LP报酬:")
        print('{:,.2f}'.format(order['LP'].sum() * lpprice))

        print("确认订单是否完成交易 Yes/No:", end="")
        confirm = input()
        if confirm == 'Yes':
            # 保存订单到表格
            writer = pd.ExcelWriter(
                'trade_record/{}_LP{}.xlsx'.format(time.strftime("%Y-%m-%d_%H%M%S", time.localtime()),
                                                   int(order['LP'].sum())))
            order.to_excel(writer, 'Sheet1')
            writer.save()

            #修改订单计划
            orderplan['Qty'] = orderplan['Qty'] - orderplan['Trade Count']
            print("打印剩余的订单计划")
            print(orderplan['Qty'])
            print('交易完成')
        else:
            print("交易撤销")
        #打印剩余的订单计划


# 天蛇
marketdata = get_marketdata("https://www.fuzzwork.co.uk/lpstore/buy/10000002/1000135", "Serpentis")
# 天使
marketdata = get_marketdata("https://www.fuzzwork.co.uk/lpstore/buy/10000002/1000124", "Archangels")
# C海军
marketdata = get_marketdata("https://www.fuzzwork.co.uk/lpstore/buy/10000002/1000035", "CaldariNavy")
# 古斯塔斯
marketdata = get_marketdata("https://www.fuzzwork.co.uk/lpstore/buy/10000002/1000127", "Guristas")
# 血袭者
marketdata = get_marketdata("https://www.fuzzwork.co.uk/lpstore/buy/10000002/1000134", "BloodRaiders")









