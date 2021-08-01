import email.mime.multipart
import email.mime.text
import smtplib
import time
import datetime
import os
import yaml

class GPURobber:

    def __init__(self, config_path='./config.yml'):
        """
            初始化邮件信息
            FROM_MAIL：用于发送邮件的邮箱
            SMTP_SERVER：FROM_MAIL 的 SMTP服务器
            SSL_PORT ： SMTP端口
            USER_PWD： SMTP授权码
            mail_list: 需要接收提醒的邮箱
        """
        self.hasGPU = self.check_gpus()
        if not self.hasGPU:
            raise Exception('GPU is not available')

        func_reflector = {
            "memory": self.memory
        }

        with open(config_path, 'rb') as f:
            temp = yaml.load(f, yaml.FullLoader)
            self.from_mail = temp['FROM_MAIL']
            self.smtp_server = temp['SMTP_SERVER']
            self.ssl_port = temp['SSL_PORT']
            self.user_pwd = temp['USER_PWD']
            self.mail_list = temp['MAIL_LIST']
            self.skip_time = temp['SKIP_TIME']
            self.trigger_mode = temp['TRIGGER_MODE']
            self.lt_mail_cd = temp["LT_MAIL_CD"]
            self.et_mail_cd = temp["ET_MAIL_CD"]
            self.query_cd = temp["QUERY_CD"]
            self.query_func = func_reflector[temp["QUERY_FUNC"]]



    def check_gpus(self):
        if not 'NVIDIA System Management' in os.popen('nvidia-smi -h').read():
            print("'nvidia-smi' tool not found.")
            return False
        return True

    def parse(self, line, qargs):
        '''
        line:
            a line of text
        qargs:
            query arguments
        return:
            a dict of gpu infos
        Pasing a line of csv format text returned by nvidia-smi
        '''
        numberic_args = ['memory.free', 'memory.total', 'power.draw', 'power.limit','temperature.gpu']
        power_manage_enable = lambda v: (not 'Not Support' in v)
        to_numberic = lambda v: float(v.upper().strip().replace('MIB', '').replace('W', ''))
        process = lambda k, v: (
            (int(to_numberic(v)) if power_manage_enable(v) else 1) if k in numberic_args else v.strip())
        return {k: process(k, v) for k, v in zip(qargs, line.strip().split(','))}

    def query_gpu(self):
        qargs = ['index', 'gpu_name', 'memory.free', 'memory.total', 'power.draw', 'power.limit', 'temperature.gpu', 'timestamp']
        cmd = 'nvidia-smi --query-gpu={} --format=csv,noheader'.format(','.join(qargs))
        results = os.popen(cmd).readlines()
        return [self.parse(line, qargs) for line in results]

    def memory(self, mem_rate=0.5):
        """
        其他筛选的方式可以根据这个方法实现，如根据power
        :param mem_rate: 内存空闲率阈值，大于该阈值的GPU才会提醒
        :return: 返回可用的gpu idx
        """
        gpus = self.query_gpu()
        gpu_idx = []
        for i in range(len(gpus)):
            if float(gpus[i]['memory.free'] / gpus[i]['memory.total']) >= mem_rate:
                gpu_idx.append(i)
        return gpu_idx

    def send_mail(self, to_mail, title, content):
        ret = True
        USER_NAME = self.from_mail  # 邮箱用户名
        msg = email.mime.multipart.MIMEMultipart()  # 实例化email对象
        msg['from'] = self.from_mail  # 对应发件人邮箱昵称、发件人邮箱账号
        msg['to'] = to_mail  # 对应收件人邮箱昵称、收件人邮箱账号
        msg['subject'] = title  # 邮件的主题
        txt = email.mime.text.MIMEText(content)
        msg.attach(txt)
        try:
            smtp = smtplib.SMTP_SSL(self.smtp_server, self.ssl_port)
            smtp.ehlo()
            smtp.login(USER_NAME, self.user_pwd)
            smtp.sendmail(self.from_mail, to_mail, str(msg))
            smtp.quit()
        except Exception as e:
            ret = False
            print(e)
        return ret

    def lever_trigger(self, query_func, mail_cd=3600, query_cd=60):
        """
        采用水平触发的方式发送邮件（即间隔固定时间发送邮件）
        :param query_func: 显卡过滤规则函数
        :param mail_cd: 发送邮件的CD
        :param query_cd: 调用query_func的频率
        :return:
        """
        while True:
            time_now = datetime.datetime.now()
            # 判断是否在不发送邮件的时间段
            if time_now.hour in self.skip_time:
                time.sleep(3600)
                continue
            # 显存剩余比例
            gpus = query_func()
            send = False
            if gpus:
                for to in self.mail_list:
                    # 隔5秒发一个人,防止被当成垃圾邮件
                    time.sleep(5)
                    send = self.send_mail(to, "GPU is available", "".join(str(gpus)))
                    print("send to " + to)
                # 如果发送成功，进入CD
                if send:
                    time.sleep(mail_cd)
            time.sleep(query_cd)

    def edge_trigger(self, query_func, mail_cd=3600, query_cd=60):
        """
        采用边缘触发的方式发送邮件（即当状态发生变化的时候才会发送邮件）
        :param query_func: 显卡过滤规则函数
        :param mail_cd: 发送邮件的CD
        :param query_cd: 调用query_func的频率
        :return:
        """
        gpus_list = []
        while True:
            time_now = datetime.datetime.now()
            # 判断是否在不发送邮件的时间段
            if time_now.hour in self.skip_time:
                time.sleep(3600)
                continue
            # 判断最近是否发过邮件
            send = False
            # 显存剩余比例
            tmp_list = query_func()
            # 如果显卡可用情况发生变化
            if gpus_list != tmp_list:
                gpus_list = tmp_list
                # 如果存在空闲的显卡才发送
                if gpus_list:
                    for to in self.mail_list:
                        # 隔5秒发一个人,防止被当成垃圾邮件
                        time.sleep(5)
                        send = self.send_mail(to, "GPU is available", "".join(str(gpus_list)))
                        print("send to " + to)
            if send:
                time.sleep(mail_cd)
            time.sleep(query_cd)

    def run(self):
        if self.trigger_mode == 'LT':
            self.lever_trigger(self.query_func, self.lt_mail_cd, self.query_cd)
        else:
            self.edge_trigger(self.query_func, self.et_mail_cd, self.query_cd)

if __name__ == "__main__":
    robber = GPURobber()
    robber.run()

