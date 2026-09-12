# پلی‌بوک‌های Narsika

این پوشه دقیقاً ۱۰ پلی‌بوک جدید و مستقل دارد: ۵ فایل در `Cisco` و ۵ فایل در `MikroTik`. هر فایل بدون `include_tasks` یا وابستگی به فایل YAML مشترک نوشته شده است. dependencyهای اجرایی در `requirements` و فایل‌های متغیر در `examples/<vendor>/` هستند.

## فهرست کاربردها

| Vendor / فایل | کاربرد | رفتار روی تجهیز |
|---|---|---|
| `Cisco/01_health_report.yml` | گزارش نسخه، IP، CPU و Interface | فقط خواندن؛ گزارش JSON روی کنترلر |
| `Cisco/02_backup_running_config.yml` | بکاپ کامل running-config | فقط خواندن؛ فایل محلی با دسترسی محدود |
| `Cisco/03_ensure_vlan.yml` | ایجاد VLAN یا اصلاح نام آن | فقط VLAN انتخاب‌شده؛ بدون تغییر عضویت پورت |
| `Cisco/04_configure_access_port.yml` | تنظیم access VLAN و توضیح یک پورت | بررسی L2، وجود VLAN و جلوگیری از تبدیل ناخواستهٔ trunk |
| `Cisco/05_ntp_syslog.yml` | افزودن مقصد NTP و Syslog | مقصدهای قبلی حفظ می‌شوند |
| `MikroTik/01_health_report.yml` | منابع سیستم، هویت، Interface و آدرس‌ها | فقط خواندن؛ قابل استفاده برای CHR |
| `MikroTik/02_export_config.yml` | خروجی متنی کانفیگ RouterOS 7 | export با مخفی بودن پیش‌فرض فیلدهای حساس |
| `MikroTik/03_ensure_vlan_interface.yml` | ایجاد Interface منطقی VLAN روی parent موجود | ایجاد در صورت نبود؛ توقف در صورت تعارض؛ بدون تغییر bridge filtering |
| `MikroTik/04_dhcp_reservation.yml` | رزرو IP برای یک MAC روی DHCP Server موجود | بررسی تعارض؛ بدون تبدیل خودکار lease داینامیک |
| `MikroTik/05_static_route.yml` | افزودن route نام‌دار در جدول main | بررسی route موجود؛ مسیر پیش‌فرض نیازمند گزینهٔ صریح |

شش پلی‌بوک قدیمی ریپو در `Original/` نگهداری شده‌اند و جزو این ده فایل جدید نیستند؛ داخل محصول نیز در Catalog با عنوان Original ثبت می‌شوند.

## پیش‌نیازها

- کنترلر Linux، WSL2 یا Docker با Python 3.12 یا جدیدتر و dependencyهای نسخهٔ فعلی؛ رابط وب از طریق سرور Flask در دسترس است.
- SSH به تجهیزات آزمایشگاهی خودتان، حساب دارای مجوز مناسب و host key تأییدشده در `known_hosts`.
- Cisco IOS/IOS XE؛ عملیات VLAN/access port برای switch دارای قابلیت L2 است، نه هر Router سیسکو. متن خروجی preflight باید با نسخهٔ تجهیز آزمایش شود.
- فایل‌های MikroTik برای **RouterOS 7** هستند. قبل از export/تغییر نسخهٔ major بررسی می‌شود. RouterOS 6 تحت پوشش این بسته نیست.

داخل همین پوشه، در محیط مجازیِ کنترلر:

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
ansible-galaxy collection install -r requirements.yml
```

نصب فقط برای اجرای پلی‌بوک است. نسخه‌های قفل‌شدهٔ collection از مستندات رسمی بررسی شده‌اند؛ سازگاری عملی این ترکیب در محیط تست فعلی تأیید نشده است. بعد از نصب نسخه‌های حل‌شده را با `ansible --version` و `ansible-galaxy collection list` ثبت کنید.

از `inventory.example.yml` یک کپی برای محیط خود بسازید و IP/usernameها را اصلاح کنید. آدرس‌های `192.0.2.0/24` و `198.51.100.0/24` در مثال‌ها برای مستندسازی‌اند و شبکهٔ شما نیستند. هیچ password واقعی یا نمونهٔ پیش‌فرض داخل inventory قرار داده نشده است.

برای password از `--ask-pass`، برای Cisco enable از `--ask-become-pass` یا از Ansible Vault استفاده کنید. رمز را داخل `--extra-vars` یا command line ننویسید. در اتصال با SSH key از تنظیمات معمول کلید استفاده کنید و گزینهٔ درخواست password را حذف کنید. host-key verification در config روشن است.

## نمونهٔ اجرای فقط خواندنی

```sh
ansible-playbook -i inventory.lab.yml Cisco/01_health_report.yml --limit cisco_lab --ask-pass --ask-become-pass
ansible-playbook -i inventory.lab.yml Cisco/02_backup_running_config.yml --limit cisco_lab --ask-pass --ask-become-pass
ansible-playbook -i inventory.lab.yml MikroTik/01_health_report.yml --limit mikrotik_lab --ask-pass
ansible-playbook -i inventory.lab.yml MikroTik/02_export_config.yml --limit mikrotik_lab --ask-pass
```

گزارش/بکاپ در پوشهٔ یکتای `artifacts/host_<name>_<random>/` روی **کنترلر** نوشته می‌شود. پوشه `0700` و فایل `0600` است. مسیر با `narsika_artifact_root` قابل تغییر است. `--check` برای collectorها عمداً رد می‌شود؛ collector باید واقعاً بخواند و فایل بسازد.

کانفیگ کامل Cisco می‌تواند اطلاعات حساس داشته باشد؛ محتوای آن با `no_log` از خروجی task پنهان می‌ماند. خروجی متنی MikroTik جایگزین binary backup نیست و passwords، certificates و SSH keys را برای بازیابی کامل در بر نمی‌گیرد. خطای export جزئی، موفقیت کامل تلقی نمی‌شود.

## پیش‌نمایش و اجرای تغییر

از فایل متغیر همان پلی‌بوک کپی بگیرید؛ نمونه:

```sh
cp examples/Cisco/03_ensure_vlan.vars.yml lab-vlan.yml
ansible-playbook -i inventory.lab.yml Cisco/03_ensure_vlan.yml --limit cisco_lab -e @lab-vlan.yml
```

در این حالت مقدار `narsika_apply: false` است؛ ورودی اعتبارسنجی می‌شود و طرح نمایش داده می‌شود، ولی برای این پلی‌بوک تغییردهنده اتصال/تغییر تجهیز انجام نمی‌شود. پس از بازبینی، در فایل متغیر مقدار را به **boolean** `true` تغییر دهید و اجرا کنید:

```sh
ansible-playbook -i inventory.lab.yml Cisco/03_ensure_vlan.yml --limit cisco_lab -e @lab-vlan.yml --ask-pass --ask-become-pass
```

برای MikroTik نیز فایل متغیر مسیر متناظر را استفاده کنید:

```sh
cp examples/MikroTik/04_dhcp_reservation.vars.yml lab-dhcp.yml
ansible-playbook -i inventory.lab.yml MikroTik/04_dhcp_reservation.yml --limit mikrotik_lab -e @lab-dhcp.yml
```

پس از اصلاح پارامترها و `narsika_apply: true`، همان دستور را با روش احراز هویت خود اجرا کنید. دکمهٔ **Preview run** داخل UI فقط شبیه‌سازی است و این دستورات CLI را اجرا نمی‌کند.

`--check` در فایل‌های تغییردهنده فقط نمایش طرح/اعتبارسنجی متغیرهاست؛ آن را dry-run کامل دستگاه یا پیش‌بینی دقیق diff تلقی نکنید. به‌ویژه ماژول RouterOS command پشتیبانی واقعی check mode ندارد. در تمام حالت‌های check، block تغییردهنده skip می‌شود.

## پارامترهای مهم

| پارامتر | توضیح |
|---|---|
| `narsika_targets` | پیش‌فرض گروه vendor؛ در runner تک‌Host می‌تواند `all` باشد |
| `narsika_apply` | فقط boolean؛ برای سه فایل تغییردهندهٔ هر vendor پیش‌فرض false |
| `narsika_artifact_root` | پوشهٔ محلی خروجی گزارش و بکاپ روی کنترلر |
| `save_config` | Cisco: پیش‌فرض false؛ true تغییر همان task را در startup-config نیز ذخیره می‌کند |
| `vlan_id`, `vlan_name` | Cisco: VLAN غیررزرو 2–4094؛ MikroTik: ID از 1 تا 4094 |
| `interface_name`, `access_vlan`, `interface_description` | نام کامل پورت L2، VLAN از قبل موجود و توضیح ASCII تک‌خطی |
| `allow_trunk_conversion` | فقط پس از بررسی نقش پورت؛ پیش‌فرض false |
| `ntp_server`, `syslog_server` | IPv4 مقصدهای موجود در محیط شما |
| `parent_interface` | parent موجود در RouterOS؛ فعال‌سازی bridge filtering یا membership انجام نمی‌شود |
| `dhcp_server`, `lease_address`, `lease_mac`, `lease_comment` | DHCP Server موجود، IPv4 رزرو، MAC استاندارد و توضیح |
| `route_name`, `route_destination`, `route_gateway`, `route_distance` | نام یکتا، شبکهٔ IPv4 canonical، gateway و distance از 1 تا 254 |
| `allow_default_route` | افزودن `/0` نیازمند true؛ پیش‌فرض false |

در RouterOS ساخت منبع غایب انجام می‌شود و اجرای دوباره روی منبع منطبق، تغییری روی آن نمی‌دهد. منبع متعارض خودکار overwrite نمی‌شود. توضیح lease موجود به‌تنهایی هم بازنویسی نمی‌شود. روی کنترلر در هر اجرای apply یک snapshot جدید ساخته می‌شود؛ بنابراین تغییر فایل محلی با idempotency تجهیز یکی نیست.

## بازگشت و محدودیت‌ها

- اجرای تغییردهنده به‌صورت `serial: 1` است و خطا ادامهٔ batch را متوقف می‌کند. این تنظیم transaction یا rollback خودکار ایجاد نمی‌کند.
- پیش از تغییر، snapshot قبلی نگهداری می‌شود. Cisco فقط فیلدهای مشخص‌شده را مدیریت می‌کند؛ اصلاح `shutdown`، voice VLAN، STP، port security یا ACL attachment در این ده فایل وجود ندارد.
- برای بازگشت، تفاوت همان operation را با snapshot بررسی و فقط تغییر موردنظر را در محیط مجاز بازگردانید. کانفیگ کامل را بدون تطبیق نسخه و توپولوژی جایگزین نکنید. حذف VLAN/route/lease تازه‌ساخته هم خودکار انجام نمی‌شود.
- افزودن VLAN منطقی MikroTik به معنی آماده شدن forwarding سراسری نیست؛ tagging، parent، bridge VLAN table و شبکهٔ بالادست باید از قبل درست باشند. DHCP باید با subnet و pool واقعی هماهنگ باشد. فعال بودن route هم به دسترس‌پذیری gateway وابسته است.
- افزودن NTP/Syslog وجود تنظیمات را مدیریت می‌کند؛ رسیدن log به سرور یا sync شدن ساعت، به سرویس/مسیر/ACL موجود بستگی دارد. `save_config` کل running-config جاری را ذخیره می‌کند، بنابراین تغییرهای ذخیره‌نشدهٔ قبلی هم ممکن است ماندگار شوند.

## وضعیت تست

ساختار YAML و ورودی‌های نمونه بررسی می‌شوند. اجرای Ansible، SSH، preflight واقعی، تغییر و rollback روی Cisco/RouterOS در این محیط انجام نشده است؛ وضعیت آن‌ها `LAB_REQUIRED` است. `ansible-playbook --syntax-check` نیز به نصب Ansible و collectionها نیاز دارد و در محیط ساخت در دسترس نبوده است. گزارش دقیق‌تر در `../docs/VALIDATION.md` قرار دارد.

## منابع رسمی بررسی‌شده

تعریف پارامترهای backup/save/match از [Cisco IOS config module](https://docs.ansible.com/projects/ansible/latest/collections/cisco/ios/ios_config_module.html) بررسی شده است. رفتار خروجی و محدودیت check mode از [RouterOS command module](https://docs.ansible.com/projects/ansible/latest/collections/community/routeros/command_module.html) و quoting پارامترها از [RouterOS quoting filters](https://docs.ansible.com/projects/ansible/latest/collections/community/routeros/docsite/quoting.html) گرفته شده‌اند.

مرجع ویژگی‌های RouterOS: [Configuration Management](https://help.mikrotik.com/docs/spaces/ROS/pages/328155/Configuration+Management)، [VLAN](https://help.mikrotik.com/docs/spaces/ROS/pages/88014957/VLAN)، [DHCP](https://help.mikrotik.com/docs/spaces/ROS/pages/24805500/DHCP) و [IP Routing](https://help.mikrotik.com/docs/spaces/ROS/pages/328084/IP+Routing). این منابع، جایگزین تست روی نسخهٔ نصب‌شدهٔ تجهیز شما نیستند. تاریخ بررسی: 2026-09-06.
