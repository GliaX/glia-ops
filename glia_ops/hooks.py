app_name = "glia_ops"
app_title = "Glia Ops"
app_publisher = "Glia"
app_description = "Operational health checks for the Glia ERPNext site"
app_email = "info@glia.org"
app_license = "MIT"

required_apps = ["frappe"]

# Backup health: alert info@glia.org when the backup CronJob stops delivering.
scheduler_events = {
	"daily": ["glia_ops.backups.daily_check"],
}
