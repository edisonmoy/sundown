$("#phone-submit").click(function () {
	var phoneNum = $("#phone-input").val();
	$.ajax({
		type: "POST",
		url: "https://moysauce18.pythonanywhere.com/api/create",
		data: { phone: phoneNum },
		error: function (error) {
			console.error(error);
		},
	});
});