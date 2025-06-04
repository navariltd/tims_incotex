## **TIMs INCOTEX Integration for ERPNext**

The **TIMs INCOTEX** integration connects **ERPNext** with the **INCOTEX TIMs** device, allowing seamless electronic tax invoice submissions to the **Kenya Revenue Authority (KRA)**. This integration ensures that every invoice created in ERPNext is sent to INCOTEX, acknowledged with a response, and then submitted to KRA, all while storing and tracking the status of each invoice.

---

### **How the Integration Works**

1.  **Invoice Creation**: Sales Invoices are created in ERPNext as usual.
2.  **Invoice Submission**: Upon submission, the invoice is automatically sent to the INCOTEX device via API.
3.  **Response Handling**: INCOTEX responds with an ID and other related information.
4.  **KRA Submission**: INCOTEX sends the data to KRA and logs the KRA response under the same ID returned to ERPNext.
5.  **Invoice Update**: ERPNext updates the invoice with CU Invoice Number, marks it as filed, and stores the verification URL for generating a QR code.

---

### **Setup Configuration**

![image](https://github.com/user-attachments/assets/4cb976e6-70b5-4e9d-b77a-ba9d4ebb97ff)

#### **1. TIMs INCOTEX Settings Doctype**

Set this up for each company:

- **Company**: This acts as the identifier; each company should have only one configuration.
- **Environment**: Choose between `Production` or `Sandbox`.
- **Base URL** and **API Key**: Provided by INCOTEX.

#### **2. API Endpoints**

Each invoice type and pricing model uses a different endpoint. Configure the following:

- **Invoice URL (Item Price Inclusive)**: For invoices where item prices include tax.
- **Invoice URL (Item Price Exclusive)**: For invoices where item prices exclude tax.
- **Credit Note URL (Item Price Inclusive)**: For inclusive tax credit notes.
- **Credit Note URL (Item Price Exclusive)**: For exclusive tax credit notes.
- **Query URL**: To confirm details of already-submitted invoices.
- **Health Check URL**: To check the availability/status of the INCOTEX device.

#### **3. Other Settings**

- **Enable Auto-Signing**: Automatically signs invoices after submission.
- **Health Check Button**: A button to manually test the device status.

---

### **HS Code Configuration**

In the **Item Doctype**:

- Go to the **Taxes** child table.
- You will now find a field called **TIMs HS Code**.
- This field allows you to set a unique HS Code per **Item Tax Template**.

![image (9)](https://github.com/user-attachments/assets/ee58bcb5-1a63-4176-881c-cd98847933bf)

- if you have many related items, you can opt tpo add teh item tax template and HS code per Item Group, we have similar taxes table there.

When you create a Sales Invoice:

- The **HS Code** will now appear **per line item**, under **Sales Invoice Item**. We have a new field hs code which is autofilled.
- This value is auto-filled based on: 1. **Item’s Tax Template**

      2.  If not available, **Item Group’s Tax Template**

      3.  If still not found, it can be **manually selected**

  ![image (10)](https://github.com/user-attachments/assets/0c44f3dc-54da-47f6-a78b-6d22aab8ca9a)

This ensures that each item sent to KRA via INCOTEX has the correct HS Code as required by TIMs.

---

### **Sales Invoice Flow**

1.  Create a **Sales Invoice** in ERPNext.
2.  Ensure the following:

    - Tax Category is selected.
    - Taxes are defined in **Sales Taxes and Charges**.
    - This determines whether to use inclusive or exclusive endpoint.

3.  Upon submission:

    - The invoice is sent to INCOTEX.
    - A response is received instantly.
    - Updates are made to the invoice:

![image (8)](https://github.com/user-attachments/assets/abb7cbce-d599-40a6-9b12-46d73ed32c69)

        -   `System Invoice Number`

        -   `CU Invoice Number`

        -   `is_filed` checkbox ticked

        -   `Verify URL` is generated

    -   A **QR Code** is created using the Verify URL.

4.  Use a **custom print format** to include the QR code on the printed invoice.(Yet to come up wit standard one).

![image (2)](https://github.com/user-attachments/assets/0fc3e577-dd1f-43c6-916a-0a7fbd2f94c1)

> 💡 **If submission fails**, ERPNext will show the failure reason, but the invoice will still be submitted locally.

---

### **Manual Re-Submission**

- Use the **ETIMs Action** button labeled **"Submit E-Invoice"** to manually resend the invoice if needed.

![Screenshot 2025-04-22 at 09 55 57](https://github.com/user-attachments/assets/202c9e0c-b10c-42d7-b9b4-c1817eac4523)

---

### **Scheduler for Auto Submission**

- A background job (scheduler) handles the auto-submission of pending invoices.

---

### **Credit Note Handling**

1.  Always create a **Credit Note** against an existing Sales Invoice.

    - The system will automatically fetch the correct `CU Invoice Number`, which is then stored in the Relevant Invoice Number field.
      ![image (3)](https://github.com/user-attachments/assets/2bbca69b-a53a-4d5e-891f-6818ea67b0ef)

2.  Upon submission:

    - The credit note is sent to INCOTEX.
    - A response is received and recorded, similar to a sales invoice.

> ⚠️ If you're creating a **standalone credit note**, **manually enter** the `CU Invoice Number` from the original Sales Invoice to ensure successful submission.
> ⚠️
> Remember, when creating an invoice ID, it must not have any special characters, so you will have to define a new name. If not, you will create a new field, which will have a new sequence that can be used as an invoice number on the INCOTEX while maintaining the customer's normal numbering.(This has yet to be done.)

Feel free to contribute.
