from webserver import WebServer
import views

# Register views with the web server
web_server = WebServer()

web_server.add_routes({"/test": views.TestView})