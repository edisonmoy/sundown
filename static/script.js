$("#phone-submit").click(function () {
    var inputVal = $("#phone-input").val();

    // Parse number via with selected country code
    var selectedCode = $("#country-code").val();
    // Validate number
    try {
        phoneNumberObj = new libphonenumber.parsePhoneNumber(
            inputVal,
            selectedCode
        );

        isValid = phoneNumberObj.isValid();
        if (isValid) {
            submit(phoneNumberObj.number);
        } else {
            displayError("Invalid phone number.");
        }
    } catch (err) {
        console.log(err);
        displayError(err.message);
    }

    async function submit(phoneNumber) {
        // Get reCaptcha token
        token = await grecaptcha.ready(function () {
            grecaptcha.execute("reCAPTCHA_site_key", { action: "submit" });
        });

        console.log("token: ", token);

        $.ajax({
            type: "POST",
            url: "https://moysauce18.pythonanywhere.com/api/create",
            data: { phone: phoneNumber, recaptcha_token: token },
            success: function () {
                displaySuccess(
                    "Success. You will receive a text message with next steps."
                );
                $("#phone-input").val("");
            },
            error: function (error) {
                displayError("Request failed. Please try again.");
                console.error(error);
            },
        });
    }
});

function displaySuccess(message) {
    var successDiv = $("#success-message");
    successDiv.html(message).show().delay(5000).fadeOut();
}

function displayError(message) {
    if (message == "INVALID_COUNTRY") {
        message = "Enter country code";
    }
    var errorDiv = $("#error-message");
    errorDiv.html(message).show().delay(3000).fadeOut();
}

$("#phone-input").on("propertychange input", function (e) {
    var valueChanged = false;

    if (e.type == "propertychange") {
        valueChanged = e.originalEvent.propertyName == "value";
    } else {
        valueChanged = true;
    }
    if (valueChanged) {
        inputVal = e.target.value;
        /* Code goes here */
        var code = "US";
        var selectedCode = $("#country-code").val();
        if (selectedCode === "US") {
            code = "US";
        } else {
            code = null;
        }
        var out = new libphonenumber.AsYouType(code).input(inputVal);
        if (code == null) {
            $(this).val(out);
        } else {
            $(this).val(out);
        }
    }
});
