var API_ENDPOINT = "https://qgu3stesgg.execute-api.eu-west-1.amazonaws.com/dev"

var answers = ["The_Scream.jpg",
    "The_Scream.jpg",
    "Kand1.jpeg",
    "Kand2.png",
    "Monet.jpg",
    "Picasso.png",
    "VanGogh.png"
];

function choose(choices) {
    var index = Math.floor(Math.random() * choices.length);
    return choices[index];
}

window.onload = loadImageInCanvas(choose(answers), document.getElementById('style_img'))

document.getElementById('style_choice').onchange = function(e) {
    loadImageInCanvas(document.getElementById("style_choice").value, document.getElementById('style_img'));
};

document.getElementById('inp').onchange = function(e) {
    loadImageInCanvas(URL.createObjectURL(this.files[0]), document.getElementById('content_img'));

}

document.getElementById("st").onclick = function() {
    document.getElementById("limit").textContent = "One second, please. Artists need time to focus and find inspiration...";
    $.ajax({
        url: API_ENDPOINT,
        type: 'POST',
        crossDomain: true,
        dataType: 'json',
        contentType: "application/json",
        success: openStyleTransferSocket,
    });
}

function openStyleTransferSocket(response) {
    var msg = JSON.parse(response);

    if (msg.dns == "limit exceeded") {
        document.getElementById("limit").textContent = "Sorry, we ran out of artistic GPUs! Can you try again in a couple of minutes?";
        return
    }

    var webSocketURL = "ws://" + msg.dns + ":8000/styletransfer"

    var maxWaitTime = 3 * 60 * 1000; // 3 minutes
    var waitFor = 500;
    var retryNo = 0;
    var increaseBy = 1.05;

    function setupWebSocket() {
        console.log("Websocket connecting to: " + webSocketURL);
        var webSocket = new WebSocket(webSocketURL);

        webSocket.onerror = function(event) {
            console.log("There was an error connecting, retry no " + retryNo);
            waitTime = waitFor * Math.pow(increaseBy, retryNo);
            if (waitTime < maxWaitTime) {
                // Reconnecting
                retryNo += 1;
                if (retryNo < 5) {
                    document.getElementById("limit").textContent = "Ok. Spinning up an artistic atelier on AWS...";
                } else if (retryNo < 10) {
                    document.getElementById("limit").textContent = "Trying to connect to our atelier...";
                } else if (retryNo < 15) {
                    document.getElementById("limit").textContent = "Waking up the artist...";
                } else if (retryNo < 20) {
                    document.getElementById("limit").textContent = "Looking for a brush...";
                } else if (retryNo < 25) {
                    document.getElementById("limit").textContent = "If only we found canvas and colors too...";
                } else if (retryNo < 30) {
                    document.getElementById("limit").textContent = "These lazy artists...";
                } else if (retryNo < 35) {
                    document.getElementById("limit").textContent = "Ok, this is getting quite time-consuming...";
                } else { document.getElementById("limit").textContent = "Hold on tight! We are almost ready..."; }
                setTimeout(setupWebSocket, waitTime);
            } else {
                // Ok we give up and we invoke the handler's error callback instead.
                webSocketHandler.onerror = function(e) { webSocketHandler.onerror(webSocket, e) };
            }
        };

        webSocket.onopen = function(e) { webSocketHandler.onopen(webSocket, e) };
        webSocket.onclose = function(e) { webSocketHandler.onclose(webSocket, e) };
        webSocket.onmessage = function(e) { webSocketHandler.onmessage(webSocket, e) };
    }

    setupWebSocket();
}

/**
 * Collection of functions designed to handle the websockets events.
 */
var webSocketHandler = {
    onopen: function(webSocket, openEvent) {
        console.log("WebSocket OPEN: " + JSON.stringify(openEvent, null, 4));
    },
    onclose: function(webSocket, closeEvent) {
        console.log("WebSocket CLOSE: " + JSON.stringify(closeEvent, null, 4));
    },
    onerror: function(webSocket, errorEvent) {
        console.log("WebSocket ERROR: " + JSON.stringify(errorEvent, null, 4));
    },
    onmessage: function(webSocket, messageEvent) {
        if (messageEvent == null) {
            webSocket.close()
        }

        console.log(messageEvent)
        var msg = JSON.parse(messageEvent.data);
        console.log(msg.state)

        switch (msg.state) {
            case "model_loaded":
                console.log("WebSocket STATE: " + msg.state);
                var to_send = {
                    action: "request_image",
                    data: {
                        content_image: base64FromCanvasId("content_img"),
                        style_image: base64FromCanvasId("style_img"),
                    }
                };
                webSocket.send(JSON.stringify(to_send));
                break;

            case "end_iteration":
                console.log("WebSocket STATE: " + msg.state);
                document.getElementById("iteration_img").src = "data:image/png;base64," + msg.data.image;
                break;

            default:
                console.log("WebSocket MESSAGE: " + msg);
        }
    }
}


function base64FromCanvasId(canvas_id) {
    return document.getElementById(canvas_id).toDataURL().split(',')[1];
}

function loadImageInCanvas(url, canvas) {
    var img = $("<img />", {
        src: url,
        crossOrigin: "Anonymous",
    }).load(draw).error(failed);

    function draw() {
        canvas.width = this.width * (300 / this.height);
        canvas.height = 300;
        var ctx = canvas.getContext('2d');
        ctx.drawImage(this, 0, 0, this.width, this.height, 0, 0, this.width * (300 / this.height), 300);
    }

    function failed() {
        alert("The provided file couldn't be loaded as an Image media");
    };

}